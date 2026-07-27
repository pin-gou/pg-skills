"""test_describe_env_protocol.py — describe_env 协议一致性测试.

覆盖范围:
  1. env-description.schema.json 是合法 JSON Schema (draft-07)
  2. examples/env-description.example.yaml 符合 schema
  3. examples/shell/hooks/describe-env.sh 必读检查完整 (5 个 PG_* vars)
  4. pg-invoke-hook.py:describe_env action 的 caller 白名单 = pg-propose / pg-fix-issue / pg-regression
  5. pg-invoke-hook.py:build_describe_env_spec 输出路径按 caller 路由
  6. pg-run-hook.py:_PG_ENV_MAP 包含 change_id / output_path 映射

跑法:
  python3 src/runtime/tests/test_describe_env_protocol.py
  或: pytest src/runtime/tests/test_describe_env_protocol.py -v
"""
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path


THIS_FILE = Path(__file__).resolve()
RUNTIME_DIR = THIS_FILE.parent.parent
OPENCODE_DIR = RUNTIME_DIR.parent.parent / "src" / "opencode"
SPEC_DIR = RUNTIME_DIR / "spec"
SCHEMA_FILE = SPEC_DIR / "env-description.schema.json"
EXAMPLE_FILE = RUNTIME_DIR.parent.parent / "examples" / "env-description.example.yaml"
HOOK_TEMPLATE = RUNTIME_DIR.parent.parent / "examples" / "shell" / "hooks" / "describe-env.sh"
INVOKE_HOOK_PY = RUNTIME_DIR / "bin" / "pg-invoke-hook.py"
RUN_HOOK_PY = RUNTIME_DIR / "lib" / "pg-run-hook.py"
PROJECT_SCHEMA = SPEC_DIR / "project.schema.json"


def _load_yaml():
    try:
        import yaml
    except ImportError:
        sys.stderr.write("Error: PyYAML required\n")
        sys.exit(2)
    return yaml


class TestEnvDescriptionSchema(unittest.TestCase):
    """env-description.schema.json 结构与合法性."""

    def test_schema_file_exists(self):
        self.assertTrue(SCHEMA_FILE.is_file(), f"schema 缺失: {SCHEMA_FILE}")

    def test_schema_is_valid_json(self):
        with open(SCHEMA_FILE, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data.get("type"), "object")

    def test_schema_required_top_keys(self):
        with open(SCHEMA_FILE, encoding="utf-8") as f:
            data = json.load(f)
        for key in ("schema_version", "described_by", "described_at", "described_for", "environments"):
            self.assertIn(key, data.get("required", []), f"top-level required 缺 {key}")

    def test_schema_defines_six_segments(self):
        with open(SCHEMA_FILE, encoding="utf-8") as f:
            data = json.load(f)
        env_block = data["definitions"]["EnvironmentBlock"]["properties"]
        for seg in (
            "infra_services", "business_systems", "data_resources",
            "config_resources", "runtime_environment", "external_dependencies",
            "relations",
        ):
            self.assertIn(seg, env_block, f"EnvironmentBlock 缺 {seg} 段")


class TestEnvDescriptionExample(unittest.TestCase):
    """examples/env-description.example.yaml 符合 schema."""

    def test_example_file_exists(self):
        self.assertTrue(EXAMPLE_FILE.is_file(), f"example 缺失: {EXAMPLE_FILE}")

    def test_example_conforms_to_schema(self):
        from jsonschema import Draft7Validator
        with open(SCHEMA_FILE, encoding="utf-8") as f:
            schema = json.load(f)
        with open(EXAMPLE_FILE, encoding="utf-8") as f:
            example_text = f.read()
        # 强制日期时间为字符串, 避免 PyYAML 自动转换
        example_text = re.sub(
            r"^(\s*)(described_at|last_verified_at):\s*([0-9TZ:-]+)$",
            r'\1\2: "\3"', example_text, flags=re.M
        )
        yaml = _load_yaml()
        example = yaml.safe_load(example_text)
        errors = list(Draft7Validator(schema).iter_errors(example))
        self.assertFalse(errors, f"example 不符合 schema: {[e.message for e in errors[:3]]}")

    def test_example_has_six_segments_populated(self):
        yaml = _load_yaml()
        with open(EXAMPLE_FILE, encoding="utf-8") as f:
            example = yaml.safe_load(f)
        env = example["environments"]["dev-local"]
        for seg in (
            "infra_services", "business_systems", "data_resources",
            "config_resources", "runtime_environment", "external_dependencies",
            "relations",
        ):
            self.assertIn(seg, env, f"example 缺 {seg}")
            self.assertGreater(len(env[seg]), 0, f"example.{seg} 为空")


class TestDescribeEnvHookTemplate(unittest.TestCase):
    """describe-env.sh 模板必读检查."""

    def test_template_exists(self):
        self.assertTrue(HOOK_TEMPLATE.is_file())

    def test_template_bash_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(HOOK_TEMPLATE)], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, f"bash 语法错: {result.stderr}")

    def test_template_reads_required_env_vars(self):
        text = HOOK_TEMPLATE.read_text(encoding="utf-8")
        for var in ("PG_RUN_CALLER", "PG_PROJECT_ROOT", "PG_CHANGE_ID",
                    "PG_ENV_NAME", "PG_OUTPUT_PATH"):
            self.assertIn(var, text, f"describe-env.sh 缺少 {var} 必读检查")

    def test_template_writes_output_path(self):
        text = HOOK_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn('"${PG_OUTPUT_PATH}"', text, "describe-env.sh 未写入 PG_OUTPUT_PATH")

    def test_template_writes_partial_on_failure(self):
        text = HOOK_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn(".partial", text, "describe-env.sh 未实现失败时写 partial 文件")

    def test_template_does_not_call_prepare_env(self):
        """Q3 决策: describe_env 与 prepare_env 独立, 不调用 prepare. 仅检查代码行 (注释允许提及)."""
        text = HOOK_TEMPLATE.read_text(encoding="utf-8")
        # 过滤代码行 (非注释行), 允许注释中说明"两脚本独立"
        code_lines = [
            line for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        code_text = "\n".join(code_lines)
        self.assertNotIn("prepare_env", code_text,
                         "describe-env.sh 代码中不应调用 prepare_env (注释除外)")


class TestProjectSchemaDescribeEnv(unittest.TestCase):
    """project.schema.json: describe_env 字段约束."""

    def test_describe_env_field_exists(self):
        with open(PROJECT_SCHEMA, encoding="utf-8") as f:
            schema = json.load(f)
        env_def = schema["definitions"]["environment"]["properties"]
        self.assertIn("describe_env", env_def, "environment 缺 describe_env 字段")

    def test_describe_env_requires_script(self):
        with open(PROJECT_SCHEMA, encoding="utf-8") as f:
            schema = json.load(f)
        describe_env = schema["definitions"]["environment"]["properties"]["describe_env"]
        self.assertIn("script", describe_env.get("required", []),
                      "describe_env.script 必须 required (Q8: 显式声明)")


class TestInvokeHookDescribeEnv(unittest.TestCase):
    """pg-invoke-hook.py: --action describe_env 实现细节."""

    def test_action_in_choices(self):
        text = INVOKE_HOOK_PY.read_text(encoding="utf-8")
        self.assertIn('"describe_env"', text, "argparse --action choices 缺 describe_env")

    def test_describe_env_in_env_level_actions(self):
        text = INVOKE_HOOK_PY.read_text(encoding="utf-8")
        m = re.search(r"ENV_LEVEL_ACTIONS\s*=\s*\((.*?)\)", text, re.DOTALL)
        self.assertIsNotNone(m, "找不到 ENV_LEVEL_ACTIONS 定义")
        self.assertIn("describe_env", m.group(1), "ENV_LEVEL_ACTIONS 缺 describe_env")

    def test_describe_env_callers_defined(self):
        text = INVOKE_HOOK_PY.read_text(encoding="utf-8")
        m = re.search(r"DESCRIBE_ENV_CALLERS\s*=\s*\((.*?)\)", text, re.DOTALL)
        self.assertIsNotNone(m, "找不到 DESCRIBE_ENV_CALLERS 定义")
        block = m.group(1)
        for caller in ("CALLER_PG_PROPOSE", "CALLER_PG_FIX_ISSUE",
                       "CALLER_PG_REGRESSION", "CALLER_AD_HOC"):
            self.assertIn(caller, block, f"DESCRIBE_ENV_CALLERS 缺 {caller}")

    def test_build_describe_env_spec_function_exists(self):
        text = INVOKE_HOOK_PY.read_text(encoding="utf-8")
        self.assertIn("def build_describe_env_spec", text,
                      "缺少 build_describe_env_spec 实现")

    def test_build_describe_env_spec_no_change_id_param(self):
        """v7: build_describe_env_spec 不再有 change_id 参数, 仅 session."""
        text = INVOKE_HOOK_PY.read_text(encoding="utf-8")
        m = re.search(r"def build_describe_env_spec\((.*?)\)", text, re.DOTALL)
        self.assertIsNotNone(m, "找不到 build_describe_env_spec 签名")
        sig = m.group(1)
        self.assertNotIn("change_id", sig,
                         "build_describe_env_spec 签名仍包含 change_id 参数 (v7 应移除)")

    def test_pg_propose_caller_in_known_callers(self):
        text = INVOKE_HOOK_PY.read_text(encoding="utf-8")
        m = re.search(r"KNOWN_CALLERS\s*=\s*\((.*?)\)", text, re.DOTALL)
        self.assertIsNotNone(m, "找不到 KNOWN_CALLERS 定义")
        self.assertIn("CALLER_PG_PROPOSE", m.group(1), "KNOWN_CALLERS 缺 CALLER_PG_PROPOSE")

    def test_ad_hoc_caller_in_known_callers(self):
        """v7: ad-hoc 进入 DESCRIBE_ENV_CALLERS 白名单."""
        text = INVOKE_HOOK_PY.read_text(encoding="utf-8")
        m = re.search(r"DESCRIBE_ENV_CALLERS\s*=\s*\((.*?)\)", text, re.DOTALL)
        self.assertIsNotNone(m, "找不到 DESCRIBE_ENV_CALLERS 定义")
        self.assertIn("CALLER_AD_HOC", m.group(1),
                      "DESCRIBE_ENV_CALLERS 未包含 CALLER_AD_HOC")

    def test_change_id_flag_removed(self):
        """v7: --change-id CLI flag 已硬删除 (Q2 决策)."""
        text = INVOKE_HOOK_PY.read_text(encoding="utf-8")
        self.assertNotIn('--change-id"', text,
                         "--change-id CLI flag 不应再存在 (v7 已硬删除)")

    def test_session_required_for_describe_env(self):
        """v7: describe_env 必填改为 --session (替代 --change-id)."""
        text = INVOKE_HOOK_PY.read_text(encoding="utf-8")
        m = re.search(
            r'elif args\.action == "describe_env":(.*?)\n        else:',
            text, re.DOTALL
        )
        self.assertIsNotNone(m, "找不到 describe_env 分支")
        block = m.group(1)
        self.assertIn("args.session", block,
                      "describe_env 分支未校验 args.session 必填")
        self.assertNotIn("args.change_id", block,
                         "describe_env 分支不应再校验 args.change_id")


class TestRunHookEnvMap(unittest.TestCase):
    """pg-run-hook.py:_PG_ENV_MAP 包含 change_id / output_path 映射."""

    def test_env_map_has_change_id(self):
        text = RUN_HOOK_PY.read_text(encoding="utf-8")
        m = re.search(r"_PG_ENV_MAP\s*=\s*\{(.*?)\}", text, re.DOTALL)
        self.assertIsNotNone(m, "找不到 _PG_ENV_MAP 定义")
        block = m.group(1)
        self.assertIn('"change_id": "PG_CHANGE_ID"', block)
        self.assertIn('"output_path": "PG_OUTPUT_PATH"', block)


class TestHookEnvVarsSSOTIntegration(unittest.TestCase):
    """hook-env-vars.yaml v6: SSOT 与注入实现一致."""

    def test_ssot_version(self):
        yaml = _load_yaml()
        with open(SPEC_DIR / "hook-env-vars.yaml", encoding="utf-8") as f:
            ssot = yaml.safe_load(f)
        self.assertEqual(ssot["version"], 6, "hook-env-vars.yaml version 必须升到 6")

    def test_ssot_includes_pg_change_id(self):
        yaml = _load_yaml()
        with open(SPEC_DIR / "hook-env-vars.yaml", encoding="utf-8") as f:
            ssot = yaml.safe_load(f)
        spec_names = {v["name"] for v in ssot["spec_injected"]}
        self.assertIn("PG_CHANGE_ID", spec_names)
        self.assertIn("PG_OUTPUT_PATH", spec_names)

    def test_pg_propose_in_caller_enum(self):
        yaml = _load_yaml()
        with open(SPEC_DIR / "hook-env-vars.yaml", encoding="utf-8") as f:
            ssot = yaml.safe_load(f)
        for entry in ssot["always_injected"]:
            if entry["name"] == "PG_RUN_CALLER":
                self.assertIn("pg-propose", entry["enum"],
                              "PG_RUN_CALLER enum 缺 pg-propose")


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)