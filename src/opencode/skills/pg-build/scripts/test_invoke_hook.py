#!/usr/bin/env python3
"""Tests for pg-invoke-hook.py (runtime-layer unified hook executor).

历史:
  v3.1 之前, invoke-hook 逻辑直接内联在 pg-pipeline-runner.py:cmd_invoke_hook,
  tests 直接 import 该函数. v3.2 抽出后, 测试目标改为:
    1. .pg/skills/src/runtime/bin/pg-invoke-hook.py  (canonical, 新代码用)
    2. .opencode/skills/pg-build/scripts/pg-pipeline-runner.py invoke-hook
       (thin wrapper, 旧代码兼容)

Covers:
- argparse: required/optional flag handling (missing required -> exit 2)
- argparse: --action choices enforcement
- argparse: --timeout / --host are NOT accepted (LLM must not pass them)
- env/role/instance/action validation: clear error messages + exit 1
- args rendering: {role}/{instance.name}/{instance.host} placeholders
- Option Y: --tail-lines appended as last 2 args (logs/tail only)
- No --tail-lines: project.yaml args used verbatim
- log_path format: role.<role>.<action>@<instance>.log under 2-build/<env>/logs
- env-level prepare_env / clean_env: log_path is env.<action>.log
- hook_type equals --action value
- Thin wrapper (pg-pipeline-runner.py invoke-hook) preserves all above behavior
  by forwarding to pg-invoke-hook.py.

Does NOT cover:
- jsonschema validation (not activated in this refactor)
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import unittest


THIS_DIR = os.path.dirname(os.path.abspath(__file__))
# This file may be reachable via different paths due to hardlinks:
#   - <project>/.opencode/skills/pg-build/scripts/test_invoke_hook.py (5 segments up to root)
#   - <project>/.pg/skills/src/opencode/skills/pg-build/scripts/test_invoke_hook.py (7 segments)
#   - /home/ubuntu/workspace/pg-skills/src/opencode/skills/pg-build/scripts/test_invoke_hook.py
#     (hardlinked, but that path has NO .pg/project.yaml — it's the upstream pg-skills
#     git checkout, not the project root)
# Walk up from THIS_DIR (and fallback to cwd) until we find .pg/project.yaml
# instead of trusting fixed relative paths.
def _find_project_root():
    env_root = os.environ.get("PG_PROJECT_ROOT")
    if env_root and os.path.isfile(os.path.join(env_root, ".pg", "project.yaml")):
        return env_root
    candidates = [THIS_DIR, os.getcwd()]
    seen = set()
    for start in candidates:
        p = start
        for _ in range(15):
            if p in seen:
                break
            seen.add(p)
            if os.path.isfile(os.path.join(p, ".pg", "project.yaml")):
                return p
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
    raise RuntimeError(
        f"Cannot find .pg/project.yaml. THIS_DIR={THIS_DIR!r}, cwd={os.getcwd()!r}. "
        f"Set PG_PROJECT_ROOT env var to the oc2-web-virt directory."
    )


PROJECT_ROOT = _find_project_root()

# Canonical executor (v3.2+, the only place where spec rendering lives).
PG_INVOKE_HOOK_PY = os.path.join(
    PROJECT_ROOT, ".pg", "skills", "src", "runtime", "bin", "pg-invoke-hook.py")

# Thin wrapper (preserved for backward compat with old prompts/tests).
RUNNER_PY = os.path.join(
    PROJECT_ROOT, ".pg", "skills", "src", "opencode", "skills",
    "pg-build", "scripts", "pg-pipeline-runner.py")

PG_RUN_HOOK_PY = os.path.join(
    PROJECT_ROOT, ".pg", "skills", "src", "runtime", "lib",
    "pg-run-hook.py")
CONFIG_PATH = os.path.join(PROJECT_ROOT, ".pg", "project.yaml")


def _load_module(path, module_name):
    """Load a .py file as a module, caching it under module_name."""
    if module_name in sys.modules:
        del sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_invoke_hook():
    """Load pg-invoke-hook.py (the canonical executor)."""
    return _load_module(PG_INVOKE_HOOK_PY, "pg_invoke_hook")


def _load_runner():
    """Load pg-pipeline-runner.py (the thin wrapper)."""
    return _load_module(RUNNER_PY, "pg_pipeline_runner")


def _run_cli_via(runner_path, *args, timeout=30):
    """Invoke the given runner via subprocess as a real CLI would."""
    r = subprocess.run(
        ["python3", runner_path, *args],
        capture_output=True, text=True, timeout=timeout,
        cwd=PROJECT_ROOT,
    )
    return r


def _run_canonical(*args, timeout=30):
    return _run_cli_via(PG_INVOKE_HOOK_PY, *args, timeout=timeout)


def _run_wrapper(*args, timeout=30):
    return _run_cli_via(RUNNER_PY, "invoke-hook", *args, timeout=timeout)


# ============================================================
# 1. Argparse basics — canonical pg-invoke-hook.py
# ============================================================

class TestArgparseBasics(unittest.TestCase):
    """Direct tests of pg-invoke-hook.py argparse layer."""

    def setUp(self):
        self.mod = _load_invoke_hook()

    def _invoke(self, argv, expect_exit=None):
        backup_argv = sys.argv[:]
        backup_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            sys.argv = ["pg-invoke-hook.py", "invoke-hook"] + argv
            try:
                self.mod.invoke_hook_main(sys.argv)
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
                if expect_exit is not None:
                    self.assertEqual(code, expect_exit)
                return ("exit", code, sys.stderr.getvalue())
            else:
                return ("ok", 0, sys.stderr.getvalue())
        finally:
            sys.argv = backup_argv
            sys.stderr = backup_stderr

    def test_missing_required_change_exits_nonzero(self):
        # v4 协议: --change / --session 不再 required; caller=ad-hoc 时 session 留空 → 自动生成.
        # 但 SKILL caller (pg-build / pg-regression / pg-fix-issue) 必须显式传 --session.
        # 此测试改为: 不传 --session + 显式 caller=pg-build → 应 exit 2.
        backup_stderr = sys.stderr
        sys.stderr = io.StringIO()
        backup_argv = sys.argv[:]
        sys.argv = ["pg-invoke-hook.py", "invoke-hook",
                    "--env", "dev-local", "--role", "backend",
                    "--instance", "backend-1", "--action", "start",
                    "--caller", "pg-build"]
        try:
            with self.assertRaises(SystemExit) as ctx:
                self.mod.invoke_hook_main(sys.argv)
            self.assertEqual(ctx.exception.code, 2)
        finally:
            sys.argv = backup_argv
            sys.stderr = backup_stderr

    def test_invalid_action_choice_exits_2(self):
        backup_stderr = sys.stderr
        sys.stderr = io.StringIO()
        backup_argv = sys.argv[:]
        sys.argv = ["pg-invoke-hook.py", "invoke-hook",
                    "--session", "x", "--env", "dev-local",
                    "--role", "backend", "--instance", "backend-1",
                    "--action", "reboot"]
        try:
            with self.assertRaises(SystemExit) as ctx:
                self.mod.invoke_hook_main(sys.argv)
            self.assertEqual(ctx.exception.code, 2)
        finally:
            sys.argv = backup_argv
            sys.stderr = backup_stderr

    def test_timeout_override_flag_is_accepted(self):
        # v4: --timeout-override 是新 CLI flag, 允许 ad-hoc 覆盖 project.yaml timeout_seconds
        # (前缀匹配会接受 --timeout = --timeout-override, 因此这里测试精确名)
        backup_stderr = sys.stderr
        sys.stderr = io.StringIO()
        backup_argv = sys.argv[:]
        sys.argv = ["pg-invoke-hook.py", "invoke-hook",
                    "--session", "x", "--env", "dev-local",
                    "--role", "backend", "--instance", "backend-1",
                    "--action", "start", "--timeout-override", "60"]
        try:
            # 不应该 raise (timeout-override 合法)
            # subprocess.run 是 stub, 不会真正跑 hook
            self.mod.invoke_hook_main(sys.argv)
        finally:
            sys.argv = backup_argv
            sys.stderr = backup_stderr

    def test_host_flag_is_rejected(self):
        backup_stderr = sys.stderr
        sys.stderr = io.StringIO()
        backup_argv = sys.argv[:]
        sys.argv = ["pg-invoke-hook.py", "invoke-hook",
                    "--session", "x", "--env", "dev-local",
                    "--role", "backend", "--instance", "backend-1",
                    "--action", "start", "--host", "remote"]
        try:
            with self.assertRaises(SystemExit) as ctx:
                self.mod.invoke_hook_main(sys.argv)
            self.assertEqual(ctx.exception.code, 2)
        finally:
            sys.argv = backup_argv
            sys.stderr = backup_stderr


# ============================================================
# 1b. v4 protocol — caller × session
# ============================================================

class TestV4Protocol(unittest.TestCase):
    """v4 协议新增行为:
       - --session 留空 + caller=ad-hoc → 自动生成 auto-<date>-<pid>
       - --skill / --caller 硬缺省 'ad-hoc'
       - 新增 --log-dir / --timeout-override
       - --change 保留为 deprecated alias (1 版本兼容, 输出 WARN)
    """

    def setUp(self):
        self.mod = _load_invoke_hook()
        self._orig_run = self.mod.subprocess.run
        self.mod.subprocess.run = lambda *a, **kw: type("R", (), {"returncode": 0})()
        self.captured_spec = None
        orig_run = self.mod.subprocess.run
        def _capture(*a, **kw):
            self.captured_spec = json.loads(kw.get("input") or a[0] if a else kw["input"])
            return type("R", (), {"returncode": 0})()
        self.mod.subprocess.run = _capture

    def tearDown(self):
        self.mod.subprocess.run = self._orig_run

    def _invoke_ok(self, *flags):
        argv = ["pg-invoke-hook.py", "invoke-hook"] + list(flags)
        backup_argv = sys.argv[:]
        backup_stderr = sys.stderr
        sys.argv = argv
        sys.stderr = io.StringIO()
        try:
            self.mod.invoke_hook_main(sys.argv)
        finally:
            sys.argv = backup_argv
            sys.stderr = backup_stderr

    def test_adhoc_auto_generates_session(self):
        # 无 --session + 无 --skill → caller=ad-hoc, session 自动生成 auto-<date>-<pid>
        self._invoke_ok(
            "--env", "dev-local",
            "--role", "backend", "--instance", "backend-1",
            "--action", "start",
        )
        spec = self.captured_spec
        self.assertEqual(spec["caller"], "ad-hoc")
        self.assertTrue(spec["session"].startswith("auto-"),
                        f"expected auto-<date>-<pid>, got {spec['session']!r}")
        # 旧 'change' key 保留作 alias
        self.assertEqual(spec["change"], spec["session"])
        # log_path 落到 .pg/ad-hoc/<session>/<env>/logs/...
        self.assertIn("/.pg/ad-hoc/", spec["log_path"])
        self.assertIn(spec["session"], spec["log_path"])

    def test_skill_caller_requires_session(self):
        # caller=pg-build 但 --session 留空 → exit 2
        backup_stderr = sys.stderr
        sys.stderr = io.StringIO()
        backup_argv = sys.argv[:]
        sys.argv = ["pg-invoke-hook.py", "invoke-hook",
                    "--env", "dev-local",
                    "--role", "backend", "--instance", "backend-1",
                    "--action", "start",
                    "--caller", "pg-build"]
        try:
            with self.assertRaises(SystemExit) as ctx:
                self.mod.invoke_hook_main(sys.argv)
            self.assertEqual(ctx.exception.code, 2)
            self.assertIn("requires explicit --session", sys.stderr.getvalue())
        finally:
            sys.argv = backup_argv
            sys.stderr = backup_stderr

    def test_change_deprecated_alias_emits_warn(self):
        # --change 仍可工作, 但 stderr 有 WARN
        backup_stderr = sys.stderr
        captured = io.StringIO()
        sys.stderr = captured
        argv = ["pg-invoke-hook.py", "invoke-hook",
                "--change", "legacy-change",
                "--env", "dev-local",
                "--role", "backend", "--instance", "backend-1",
                "--action", "start"]
        backup_argv = sys.argv[:]
        sys.argv = argv
        try:
            self.mod.invoke_hook_main(sys.argv)
        finally:
            sys.argv = backup_argv
            sys.stderr = backup_stderr
        err = captured.getvalue()
        self.assertIn("deprecated", err)
        # 同时 'change' 字段写入 spec (alias)
        self.assertEqual(self.captured_spec["change"], "legacy-change")
        self.assertEqual(self.captured_spec["session"], "legacy-change")

    def test_log_dir_override(self):
        # --log-dir 覆盖路由
        self._invoke_ok(
            "--env", "dev-local",
            "--role", "backend", "--instance", "backend-1",
            "--action", "start",
            "--log-dir", "/tmp/dbg-test",
        )
        spec = self.captured_spec
        self.assertEqual(spec["hook_log_dir"], "/tmp/dbg-test")
        self.assertTrue(spec["log_path"].startswith("/tmp/dbg-test/"),
                        f"expected /tmp/dbg-test/, got {spec['log_path']!r}")
        self.assertTrue(spec["hook_result_path"].startswith("/tmp/dbg-test/"))

    def test_timeout_override_emits_warn(self):
        backup_stderr = sys.stderr
        captured = io.StringIO()
        sys.stderr = captured
        argv = ["pg-invoke-hook.py", "invoke-hook",
                "--env", "dev-local",
                "--role", "backend", "--instance", "backend-1",
                "--action", "start",
                "--timeout-override", "60"]
        backup_argv = sys.argv[:]
        sys.argv = argv
        try:
            self.mod.invoke_hook_main(sys.argv)
        finally:
            sys.argv = backup_argv
            sys.stderr = backup_stderr
        err = captured.getvalue()
        self.assertIn("--timeout-override=60", err)
        self.assertIn("WARN", err)
        self.assertEqual(self.captured_spec["timeout_seconds"], 60)

    def test_caller_alias_works(self):
        # --caller 与 --skill 等价 (互为 alias)
        self._invoke_ok(
            "--session", "demo-change",
            "--env", "dev-local",
            "--role", "backend", "--instance", "backend-1",
            "--action", "start",
            "--caller", "pg-regression",
        )
        self.assertEqual(self.captured_spec["caller"], "pg-regression")
        self.assertIn("/.pg/regression/demo-change/", self.captured_spec["log_path"])

    def test_hook_result_path_in_spec(self):
        # spec 包含 hook_result_path (修 D5)
        self._invoke_ok(
            "--session", "demo-change",
            "--env", "dev-local",
            "--role", "backend", "--instance", "backend-1",
            "--action", "start",
        )
        spec = self.captured_spec
        self.assertIn("hook_result_path", spec)
        self.assertTrue(spec["hook_result_path"].endswith(".result.json"))


# ============================================================
# 2. Spec rendering — canonical pg-invoke-hook.py
# ============================================================

class TestSpecRendering(unittest.TestCase):
    """Verify the spec passed to pg-run-hook.py matches expectations.

    Strategy: monkey-patch subprocess.run inside the module so we
    can capture the spec without actually executing the hook.
    """

    from typing import Any  # noqa: F401
    captured_spec: "dict[str, Any]"
    captured_cmd: "list[Any]"
    _orig_run: "Any"
    mod: "Any"

    def setUp(self):
        self.mod = _load_invoke_hook()
        self.captured_spec = {}
        self.captured_cmd = []
        self._orig_run = self.mod.subprocess.run

        def fake_run(cmd, **kwargs):
            self.captured_cmd = cmd
            inp = kwargs.get("input")
            if isinstance(inp, str) and inp:
                self.captured_spec = json.loads(inp)
            else:
                self.captured_spec = {}

            class _R:
                returncode = 0
            return _R()
        self.mod.subprocess.run = fake_run

    def tearDown(self):
        self.mod.subprocess.run = self._orig_run

    def _invoke_ok(self, *args):
        backup_argv = sys.argv[:]
        sys.argv = ["pg-invoke-hook.py", "invoke-hook", *args]
        try:
            try:
                rc = self.mod.invoke_hook_main(sys.argv)
                self.assertEqual(rc, 0, msg=f"invoke_hook_main returned {rc}")
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
                self.assertEqual(code, 0, msg=f"invoke_hook_main exited {code}")
        finally:
            sys.argv = backup_argv

    def test_basic_spec_shape(self):
        self._invoke_ok(
            "--session", "add-host-memory-overview",
            "--env", "dev-local",
            "--role", "backend", "--instance", "backend-1",
            "--action", "start",
        )
        spec = self.captured_spec
        for k in ("cmd", "change", "stage", "env", "role",
                  "instance_name", "instance_host", "hook_type",
                  "timeout_seconds", "log_path"):
            self.assertIn(k, spec, f"spec missing field {k!r}")
        self.assertEqual(spec["change"], "add-host-memory-overview")
        self.assertEqual(spec["env"], "dev-local")
        self.assertEqual(spec["role"], "backend")
        self.assertEqual(spec["instance_name"], "backend-1")
        self.assertEqual(spec["hook_type"], "start")
        self.assertEqual(spec["stage"], "manual")
        self.assertEqual(spec["instance_host"], "localhost")

    def test_log_path_format(self):
        # v4: 无 --skill / --caller → caller 默认 'ad-hoc' → .pg/ad-hoc/<session>/<env>/logs/...
        self._invoke_ok(
            "--session", "add-host-memory-overview",
            "--env", "dev-local",
            "--role", "backend", "--instance", "backend-1",
            "--action", "start",
        )
        expected = os.path.join(
            PROJECT_ROOT,
            ".pg/ad-hoc/add-host-memory-overview/dev-local/logs",
            "role.backend.start@backend-1.log",
        )
        self.assertEqual(self.captured_spec["log_path"], expected)

    def test_log_path_pg_regression_skill(self):
        # pg-regression → .pg/regression/<session>/<env>/logs/...
        # (v4: session 保留 regression-<suite> 前缀, 不再 strip)
        self._invoke_ok(
            "--session", "regression-backend",
            "--env", "dev-local",
            "--role", "backend", "--instance", "backend-1",
            "--action", "start",
            "--skill", "pg-regression",
        )
        expected = os.path.join(
            PROJECT_ROOT,
            ".pg/regression/regression-backend/dev-local/logs",
            "role.backend.start@backend-1.log",
        )
        self.assertEqual(self.captured_spec["log_path"], expected)
        self.assertEqual(self.captured_spec["caller"], "pg-regression")

    def test_log_path_pg_fix_issue_skill(self):
        # pg-fix-issue → .pg/fix-issue/<session>/<env>/logs/...
        self._invoke_ok(
            "--session", "fix-2026-06-26-vm-failure",
            "--env", "dev-local",
            "--role", "backend", "--instance", "backend-1",
            "--action", "start",
            "--skill", "pg-fix-issue",
        )
        expected = os.path.join(
            PROJECT_ROOT,
            ".pg/fix-issue/fix-2026-06-26-vm-failure/dev-local/logs",
            "role.backend.start@backend-1.log",
        )
        self.assertEqual(self.captured_spec["log_path"], expected)
        self.assertEqual(self.captured_spec["caller"], "pg-fix-issue")

    def test_log_path_env_level_pg_regression(self):
        # env-level + pg-regression → .pg/regression/<session>/<env>/logs/env.<action>.log
        self._invoke_ok(
            "--session", "regression-frontend",
            "--env", "dev-local",
            "--action", "prepare_env",
            "--skill", "pg-regression",
        )
        expected = os.path.join(
            PROJECT_ROOT,
            ".pg/regression/regression-frontend/dev-local/logs",
            "env.prepare_env.log",
        )
        self.assertEqual(self.captured_spec["log_path"], expected)

    def test_instance_host_replaced_in_args(self):
        # project.yaml has: actions.start.args = ["{role}", "{instance.name}", "--grpc"]
        # for backend (no instance.host placeholder in this action's args).
        self._invoke_ok(
            "--session", "add-host-memory-overview",
            "--env", "dev-local",
            "--role", "backend", "--instance", "backend-1",
            "--action", "start",
        )
        cmd = self.captured_spec["cmd"]
        self.assertIn("backend", cmd)
        self.assertIn("backend-1", cmd)
        self.assertIn("--grpc", cmd)
        for ph in ("{role}", "{instance.name}", "{instance.host}"):
            self.assertNotIn(ph, cmd, f"unrendered placeholder {ph} in cmd")

    def test_tail_lines_appended_for_logs(self):
        # backend.logs has args = ["{lines:100}"] in project.yaml.
        # Without --tail-lines, the literal {lines:100} stays as-is.
        self._invoke_ok(
            "--session", "add-host-memory-overview",
            "--env", "dev-local",
            "--role", "backend", "--instance", "backend-1",
            "--action", "logs",
        )
        cmd_no_flag = self.captured_spec["cmd"]
        self.assertIn("{lines:100}", cmd_no_flag,
                      "without --tail-lines, args should keep {lines:100}")
        self.assertNotIn("--tail-lines", cmd_no_flag)

        # With --tail-lines, runner appends --tail-lines N to args.
        self._invoke_ok(
            "--session", "add-host-memory-overview",
            "--env", "dev-local",
            "--role", "backend", "--instance", "backend-1",
            "--action", "logs",
            "--tail-lines", "200",
        )
        cmd_with_flag = self.captured_spec["cmd"]
        self.assertIn("--tail-lines", cmd_with_flag)
        self.assertIn("200", cmd_with_flag)

    def test_tail_lines_ignored_for_start(self):
        self._invoke_ok(
            "--session", "add-host-memory-overview",
            "--env", "dev-local",
            "--role", "backend", "--instance", "backend-1",
            "--action", "start",
            "--tail-lines", "200",
        )
        self.assertNotIn("--tail-lines", self.captured_spec["cmd"])

    def test_hook_type_equals_action_for_all_actions(self):
        for action in ("start", "stop", "logs", "tail"):
            self._invoke_ok(
                "--session", "add-host-memory-overview",
                "--env", "dev-local",
                "--role", "backend", "--instance", "backend-1",
                "--action", action,
            )
            self.assertEqual(self.captured_spec["hook_type"], action,
                             f"hook_type mismatch for action={action}")

    def test_prepare_env_spec_shape(self):
        # Environment-level hook: no role/instance, hook_type=prepare_env,
        # log_path is env.prepare_env.log under 2-build/<env>/logs.
        self._invoke_ok(
            "--session", "add-host-memory-overview",
            "--env", "dev-local",
            "--action", "prepare_env",
        )
        spec = self.captured_spec
        self.assertEqual(spec["role"], "")
        self.assertEqual(spec["instance_name"], "")
        self.assertEqual(spec["instance_host"], "")
        self.assertEqual(spec["hook_type"], "prepare_env")
        expected_log = os.path.join(
            PROJECT_ROOT,
            ".pg/ad-hoc/add-host-memory-overview/dev-local/logs",
            "env.prepare_env.log",
        )
        self.assertEqual(spec["log_path"], expected_log)
        self.assertTrue(spec["cmd"].startswith("bash "),
                        f"env-level cmd should start with 'bash ', got: {spec['cmd']!r}")


# ============================================================
# 3. Validation errors — canonical pg-invoke-hook.py
# ============================================================

class TestValidationErrors(unittest.TestCase):
    """Error paths: missing --role, missing --instance, unknown env/role/instance/action."""

    from typing import Any  # noqa: F401
    mod: "Any"

    def setUp(self):
        self.mod = _load_invoke_hook()
        # stub to prevent accidental real invocation
        self._orig_run = self.mod.subprocess.run
        self.mod.subprocess.run = lambda *a, **kw: type("R", (), {"returncode": 0})()

    def tearDown(self):
        self.mod.subprocess.run = self._orig_run

    def _expect_exit1(self, *flags):
        defaults = {
            "--session": "x",
            "--env": "dev-local",
            "--role": "backend",
            "--instance": "backend-1",
            "--action": "start",
        }
        i = 0
        while i < len(flags):
            defaults[flags[i]] = flags[i + 1]
            i += 2

        argv = ["pg-invoke-hook.py", "invoke-hook"]
        for k, v in defaults.items():
            argv.extend([k, v])

        backup_argv = sys.argv[:]
        sys.argv = argv
        backup_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = self.mod.invoke_hook_main(sys.argv)
            self.assertEqual(rc, 1, msg=f"expected exit 1, got {rc}")
            err = sys.stderr.getvalue()
            return err
        finally:
            sys.argv = backup_argv
            sys.stderr = backup_stderr

    def test_per_role_action_missing_role(self):
        # start is per-role, --role must be present.
        backup_argv = sys.argv[:]
        sys.argv = ["pg-invoke-hook.py", "invoke-hook",
                    "--session", "x", "--env", "dev-local",
                    "--instance", "backend-1", "--action", "start"]
        backup_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = self.mod.invoke_hook_main(sys.argv)
            self.assertEqual(rc, 1)
            self.assertIn("requires --role", sys.stderr.getvalue())
        finally:
            sys.argv = backup_argv
            sys.stderr = backup_stderr

    def test_per_role_action_missing_instance(self):
        backup_argv = sys.argv[:]
        sys.argv = ["pg-invoke-hook.py", "invoke-hook",
                    "--session", "x", "--env", "dev-local",
                    "--role", "backend", "--action", "start"]
        backup_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = self.mod.invoke_hook_main(sys.argv)
            self.assertEqual(rc, 1)
            self.assertIn("requires --instance", sys.stderr.getvalue())
        finally:
            sys.argv = backup_argv
            sys.stderr = backup_stderr

    def test_unknown_env(self):
        err = self._expect_exit1("--env", "ghost-env")
        self.assertIn("ghost-env", err)

    def test_unknown_role(self):
        err = self._expect_exit1("--role", "ghost-role")
        self.assertIn("ghost-role", err)

    def test_unknown_instance(self):
        err = self._expect_exit1("--instance", "ghost-instance")
        self.assertIn("ghost-instance", err)


# ============================================================
# 4. Thin wrapper behavior — pg-pipeline-runner.py invoke-hook
# ============================================================

class TestThinWrapperBehavior(unittest.TestCase):
    """Verify pg-pipeline-runner.py invoke-hook forwards to pg-invoke-hook.py.

    The thin wrapper must:
    - Accept the same CLI flags as the canonical executor
    - Exit code == pg-invoke-hook.py exit code
    - Stderr messages come from the canonical executor
    """

    def test_wrapper_argparse_rejects_invalid_action(self):
        # Old wrapper must still raise SystemExit(2) for invalid action.
        backup_stderr = sys.stderr
        sys.stderr = io.StringIO()
        backup_argv = sys.argv[:]
        sys.argv = ["runner.py", "invoke-hook",
                    "--session", "x", "--env", "dev-local",
                    "--role", "backend", "--instance", "backend-1",
                    "--action", "reboot"]
        try:
            mod = _load_runner()
            with self.assertRaises(SystemExit) as ctx:
                mod.cmd_invoke_hook(sys.argv)
            self.assertEqual(ctx.exception.code, 2)
        finally:
            sys.argv = backup_argv
            sys.stderr = backup_stderr

    def test_wrapper_forwards_validation_error(self):
        # Unknown role should produce the same error path as canonical.
        r = _run_wrapper(
            "--session", "x", "--env", "dev-local",
            "--role", "ghost-role",
            "--instance", "ghost",
            "--action", "start",
        )
        self.assertEqual(r.returncode, 1, msg=r.stderr)
        self.assertIn("ghost-role", r.stderr)

    def test_wrapper_help(self):
        # The thin wrapper should NOT have its own --help (it forwards to
        # canonical, which prints help). Both wrappers are now CLI tools;
        # the canonical executor prints argparse help, and `python3 runner.py
        # invoke-hook --help` returns exit 0 with usage info.
        r = _run_wrapper("--help", timeout=10)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("usage:", r.stdout + r.stderr)


# ============================================================
# 5. End-to-end smoke — canonical CLI via subprocess
# ============================================================

class TestEndToEndInvokeHook(unittest.TestCase):
    """Real CLI invocation. Uses --action prepare_env which is idempotent
    enough to safely exercise the full subprocess + spec pipe path.
    """

    def test_canonical_cli_unknown_role_exits_1(self):
        r = _run_canonical(
            "--session", "x", "--env", "dev-local",
            "--role", "ghost-role",
            "--instance", "ghost",
            "--action", "start",
        )
        self.assertEqual(r.returncode, 1, msg=r.stderr)
        self.assertIn("ghost-role", r.stderr)

    def test_wrapper_cli_unknown_role_exits_1(self):
        r = _run_wrapper(
            "--session", "x", "--env", "dev-local",
            "--role", "ghost-role",
            "--instance", "ghost",
            "--action", "start",
        )
        self.assertEqual(r.returncode, 1, msg=r.stderr)
        self.assertIn("ghost-role", r.stderr)


if __name__ == "__main__":
    unittest.main()
