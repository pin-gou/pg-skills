"""test_hook_env_vars_ssot.py — 校验 hook-env-vars.yaml SSOT 与注入实现一致.

SSOT: src/runtime/spec/hook-env-vars.yaml
注入实现: src/runtime/lib/pg-run-hook.py:_PG_ENV_MAP + build_env()
共享库引用: src/runtime/lib/hook-helpers.sh (pg_fail / pg_exit)

校验项:
  1. _PG_ENV_MAP 的 spec_key 集合 ⊆ YAML.always_injected ∪ YAML.spec_injected
  2. YAML.spec_injected 的 spec_key 集合 ⊆ _PG_ENV_MAP 的 spec_key 集合
  3. _PG_ENV_MAP 不注入任何 YAML.removed 中的 deprecated var
  4. build_env() 必填项 (PG_PROJECT_ROOT / PG_SKILLS_PATH / PG_RUN_CALLER) 与 YAML.always_injected 一致
  5. .pg/hooks/lib/common.sh 不引用任何 deprecated alias
     (PG_SKILL_NAME / PG_CHANGE_NAME / PG_MODULE / PG_MODULE_ROOT)

跑法:
  cd /home/ubuntu/workspace/pg-skills
  python3 src/runtime/tests/test_hook_env_vars_ssot.py
  或: pytest src/runtime/tests/test_hook_env_vars_ssot.py -v
"""
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path


# 路径常量 (兼容 pytest / 直接 python 两种跑法)
THIS_FILE = Path(__file__).resolve()
RUNTIME_DIR = THIS_FILE.parent.parent
SPEC_FILE = RUNTIME_DIR / "spec" / "hook-env-vars.yaml"
RUN_HOOK_PY = RUNTIME_DIR / "lib" / "pg-run-hook.py"
HOOK_HELPERS_SH = RUNTIME_DIR / "lib" / "hook-helpers.sh"

# 项目本地副本 (非 SSOT, 仅校验一致性)
def _find_project_hooks_common():
    """Locate the project's .pg/hooks/lib/common.sh via env / cwd fallback."""
    candidates = []
    pg_root = os.environ.get("PG_PROJECT_ROOT")
    if pg_root:
        candidates.append(Path(pg_root) / ".pg" / "hooks" / "lib" / "common.sh")
    candidates.append(Path.cwd() / ".pg" / "hooks" / "lib" / "common.sh")
    for c in candidates:
        if c.is_file():
            return c
    return None


PROJECT_HOOKS_COMMON = _find_project_hooks_common()


def _load_yaml():
    try:
        import yaml
    except ImportError:
        sys.stderr.write(
            "Error: PyYAML is required. Install via `pip install pyyaml`.\n"
        )
        sys.exit(2)
    return yaml


def _load_ssot():
    """Load hook-env-vars.yaml SSOT."""
    yaml = _load_yaml()
    with open(SPEC_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _extract_pg_env_map():
    """从 pg-run-hook.py 静态解析 _PG_ENV_MAP 的 spec_key 集合.

    不 import (避免污染 runtime), 用 AST/regex 静态扫描.
    """
    text = RUN_HOOK_PY.read_text(encoding="utf-8")
    # 匹配 _PG_ENV_MAP = { ... } 块
    m = re.search(
        r"_PG_ENV_MAP\s*=\s*\{(.*?)\}",
        text,
        re.DOTALL,
    )
    if not m:
        raise AssertionError(f"pg-run-hook.py 找不到 _PG_ENV_MAP 定义")
    block = m.group(1)
    # 提取所有 "key": "value" 形式
    pairs = re.findall(r'["\']([\w_]+)["\']\s*:\s*["\']([\w_]+)["\']', block)
    return dict(pairs)  # spec_key -> env_var


def _extract_build_env_always_injected():
    """从 pg-run-hook.py:build_env() 静态解析 always-injected 集合.

    扫描 `env["..."] = ...` 或 `env.setdefault("...", ...)` 行.
    """
    text = RUN_HOOK_PY.read_text(encoding="utf-8")
    m = re.search(
        r"def build_env\(spec\):.*?(?=\ndef |\nclass |\Z)",
        text,
        re.DOTALL,
    )
    if not m:
        raise AssertionError("pg-run-hook.py 找不到 build_env() 定义")
    fn_body = m.group(0)
    # env["PG_X"] = ...
    direct = set(re.findall(r'env\[["\'](\w+)["\']\]\s*=', fn_body))
    # env.setdefault("PG_X", ...)
    setdefault = set(re.findall(r'env\.setdefault\(\s*["\'](\w+)["\']', fn_body))
    return direct | setdefault


class TestHookEnvVarsSSOT(unittest.TestCase):
    """hook-env-vars.yaml 与 pg-run-hook.py:_PG_ENV_MAP / build_env 一致性."""

    @classmethod
    def setUpClass(cls):
        cls.ssot = _load_ssot()
        cls.env_map = _extract_pg_env_map()
        cls.always_injected = _extract_build_env_always_injected()

    def test_ssot_file_exists(self):
        self.assertTrue(SPEC_FILE.is_file(), f"SSOT 不存在: {SPEC_FILE}")

    def test_ssot_has_required_top_keys(self):
        for key in ("version", "scope", "always_injected", "spec_injected", "removed"):
            self.assertIn(key, self.ssot, f"SSOT 缺 {key} 段")

    def test_env_map_spec_keys_in_ssot(self):
        """_PG_ENV_MAP 的每个 spec_key 必须出现在 SSOT 的 always_injected 或 spec_injected."""
        ssot_always = {v["name"] for v in self.ssot["always_injected"]}
        ssot_spec = {v["spec_key"] for v in self.ssot["spec_injected"]}
        # env_map 是 spec_key -> env_var; 我们校验 spec_key 存在
        for spec_key, env_var in self.env_map.items():
            # spec_key 在 spec_injected 中, 或者 env_var 在 always_injected 中
            in_spec = spec_key in ssot_spec
            in_always = env_var in ssot_always
            self.assertTrue(
                in_spec or in_always,
                f"_PG_ENV_MAP['{spec_key}'] -> '{env_var}' 不在 SSOT 中 "
                f"(spec_injected.spec_keys={ssot_spec}, always_injected.names={ssot_always})",
            )

    def test_ssot_spec_keys_all_in_env_map(self):
        """SSOT.spec_injected 的每个 spec_key 必须出现在 _PG_ENV_MAP."""
        ssot_spec_keys = {v["spec_key"] for v in self.ssot["spec_injected"]}
        env_map_keys = set(self.env_map.keys())
        missing = ssot_spec_keys - env_map_keys
        self.assertFalse(
            missing,
            f"SSOT.spec_injected 中的 spec_key {missing} 缺失于 _PG_ENV_MAP",
        )

    def test_env_map_does_not_inject_removed_vars(self):
        """_PG_ENV_MAP 不应注入任何 SSOT.removed 中的 deprecated var."""
        removed_names = set(self.ssot["removed"])
        injected_env_vars = set(self.env_map.values())
        forbidden = removed_names & injected_env_vars
        self.assertFalse(
            forbidden,
            f"_PG_ENV_MAP 仍在注入已废弃 vars: {forbidden}",
        )

    def test_always_injected_matches_ssot(self):
        """build_env() 的硬注入 vars 必须 ⊇ SSOT.always_injected 的 name."""
        ssot_always = {v["name"] for v in self.ssot["always_injected"]}
        missing = ssot_always - self.always_injected
        self.assertFalse(
            missing,
            f"build_env() 漏注入 SSOT.always_injected 中的 vars: {missing}",
        )

    def test_no_deprecated_alias_in_always_injected(self):
        """build_env() 硬注入集合不应包含 deprecated alias."""
        removed_names = set(self.ssot["removed"])
        forbidden = removed_names & self.always_injected
        self.assertFalse(
            forbidden,
            f"build_env() 硬注入了已废弃 vars: {forbidden}",
        )

    def test_hook_helpers_sh_references_no_deprecated(self):
        """hook-helpers.sh 不应引用任何 deprecated PG_* var."""
        if not HOOK_HELPERS_SH.is_file():
            self.skipTest(f"{HOOK_HELPERS_SH} 不存在")
        text = HOOK_HELPERS_SH.read_text(encoding="utf-8")
        removed = set(self.ssot["removed"])
        for var in removed:
            pattern = rf"\b{re.escape(var)}\b"
            self.assertNotRegex(
                text, pattern,
                f"hook-helpers.sh 引用了已废弃 var {var}",
            )


class TestProjectCommonSh(unittest.TestCase):
    """项目本地副本 .pg/hooks/lib/common.sh 不引用 deprecated alias."""

    def test_project_common_exists(self):
        self.assertIsNotNone(
            PROJECT_HOOKS_COMMON,
            "项目本地副本路径解析失败",
        )
        assert PROJECT_HOOKS_COMMON is not None  # for type checker
        if not PROJECT_HOOKS_COMMON.is_file():
            self.skipTest(f"项目本地副本 {PROJECT_HOOKS_COMMON} 不存在, 跳过")
        # 只校验代码行 (非注释行), 允许在文档/changelog 注释中提及已废弃 var 名.
        ssot = _load_ssot()
        text = PROJECT_HOOKS_COMMON.read_text(encoding="utf-8")
        code_lines = [
            line for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        code_text = "\n".join(code_lines)
        for var in ("PG_SKILL_NAME", "PG_CHANGE_NAME", "PG_MODULE", "PG_MODULE_ROOT"):
            pattern = rf"\b{re.escape(var)}\b"
            self.assertNotRegex(
                code_text, pattern,
                f"{PROJECT_HOOKS_COMMON} 在代码中引用了已废弃 var {var} (注释中允许提及)",
            )


class TestBashSyntax(unittest.TestCase):
    """所有 hook 模板 + lib/common.sh 必须 bash 语法正确."""

    def test_all_bash_syntax(self):
        paths = [
            RUNTIME_DIR.parent / "examples" / "shell" / "hooks" / "lib" / "common.sh",
            RUNTIME_DIR.parent / "examples" / "shell" / "hooks" / "role-start.sh",
            RUNTIME_DIR.parent / "examples" / "shell" / "hooks" / "role-stop.sh",
            RUNTIME_DIR.parent / "examples" / "shell" / "hooks" / "role-logs.sh",
            RUNTIME_DIR.parent / "examples" / "shell" / "hooks" / "env-prepare.sh",
            RUNTIME_DIR.parent / "examples" / "shell" / "hooks" / "env-clean.sh",
            HOOK_HELPERS_SH,
        ]
        for p in paths:
            if not p.is_file():
                continue
            result = subprocess.run(
                ["bash", "-n", str(p)],
                capture_output=True, text=True,
            )
            self.assertEqual(
                result.returncode, 0,
                f"{p} bash 语法错: {result.stderr}",
            )


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)