"""Tests for invoke-hook CLI supporting environment-level actions.

历史:
  v3.1 之前, 这个文件直接 import pg-pipeline-runner.py:cmd_invoke_hook 并 mock
  subprocess.run, 验证 env-level prepare_env / clean_env 的 spec 渲染.
  v3.2 抽到 runtime 层独立 CLI pg-invoke-hook.py 后, 测试目标改为:
    - 加载 .pg/skills/src/runtime/bin/pg-invoke-hook.py (canonical)
    - 加载 .opencode/skills/pg-build/scripts/pg-pipeline-runner.py (thin wrapper)

Validates the env-level expansion of invoke-hook:
- `--action prepare_env` / `--action clean_env` are accepted
- These actions skip role/instance validation
- The spec built for env-level actions has empty role/instance_host
  but well-formed cmd/log_path/hook_type
- Args from `environments.<env>.prepare_env.args` are rendered into cmd
- Per-role actions still work (regression check)
- Thin wrapper (pg-pipeline-runner.py invoke-hook) forwards correctly

Strategy: drive invoke_hook_main directly with patched sys.argv + load_config,
capturing the spec passed to pg-run-hook.py (we mock subprocess.run so no
real hook execution happens).
"""
import importlib.util
import json
import os
import pathlib
import sys
import unittest
from unittest import mock


def _find_project_root(here):
    """Walk up from `here` until we find a directory containing .pg/project.yaml."""
    p = os.path.dirname(here)
    for _ in range(10):
        if os.path.isfile(os.path.join(p, ".pg", "project.yaml")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    raise RuntimeError(
        f"Cannot find .pg/project.yaml walking up from {here}")


HERE = os.path.abspath(__file__)
PROJECT_ROOT = _find_project_root(HERE)

# Canonical executor (v3.2+, 唯一渲染 spec 的地方)
PG_INVOKE_HOOK_PY = os.path.join(
    PROJECT_ROOT, ".pg", "skills", "src", "runtime", "bin", "pg-invoke-hook.py",
)
# Thin wrapper (向后兼容, 转发到 canonical)
RUNNER_PATH = os.path.join(
    PROJECT_ROOT, ".opencode", "skills", "pg-build", "scripts", "pg-pipeline-runner.py",
)


def _load_module(path, module_name):
    """Import a Python file as a module from absolute path."""
    if module_name in sys.modules:
        del sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None, f"Cannot load spec for {path}"
    assert spec.loader is not None, "loader is None"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_canonical():
    """Load the canonical pg-invoke-hook.py module."""
    return _load_module(PG_INVOKE_HOOK_PY, "pg_invoke_hook")


def _load_wrapper():
    """Load the thin wrapper (pg-pipeline-runner.py) module.

    runner.py imports sibling helpers (pg_context_chain, pg_pipeline_common,
    pg_pipeline_state) at module top level. We must add the scripts dir
    to sys.path before importing.
    """
    scripts_dir = os.path.dirname(RUNNER_PATH)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    return _load_module(RUNNER_PATH, "pg_pipeline_runner")


# Sample config used by the tests
SAMPLE_CONFIG = {
    "environments": {
        "dev-local": {
            "prepare_env": {
                "script": ".pg/hooks/env-dev-local-prepare.sh",
                "args": [],
                "timeout_seconds": 300,
            },
            "clean_env": {
                "script": ".pg/hooks/env-dev-local-clean.sh",
                "args": ["--force"],
                "timeout_seconds": 120,
            },
            "roles": {
                "backend": {
                    "instances": [
                        {"name": "backend-1", "host": "localhost", "port": 9080},
                    ],
                    "actions": {
                        "start": {
                            "script": ".pg/hooks/role-backend-start.sh",
                            "args": ["{role}", "{instance.name}"],
                            "timeout_seconds": 60,
                        },
                    },
                },
            },
        },
    },
}


class TestInvokeHookEnvLevelActions(unittest.TestCase):
    """invoke-hook must accept prepare_env / clean_env as actions.

    Drives the canonical pg-invoke-hook.py module.
    """

    def setUp(self):
        self.mod = _load_canonical()
        self.captured_spec = None
        # find_project_root returns Path; the module uses Path operations.
        self.project_root_path = pathlib.Path(PROJECT_ROOT)

    def _patched_run(self, returncode=0):
        """Return a side_effect for subprocess.run that captures the spec.

        pg-invoke-hook.py pipes the spec as JSON to subprocess.run's
        stdin. We patch subprocess.run inside the module so we can
        inspect the spec without invoking pg-run-hook.py.

        The returned object MUST have a real `.returncode` attribute (an int),
        because invoke_hook_main does return proc.returncode.
        """
        def fake_run(argv, input=None, **kwargs):
            self.captured_spec = json.loads(input)
            return mock.Mock(returncode=returncode)

        return fake_run

    def _call(self, *args):
        """Invoke invoke_hook_main with sys.argv = ["pg-invoke-hook.py", "invoke-hook", *args].

        Returns the int returned by invoke_hook_main. Uses SAMPLE_CONFIG to
        isolate tests from the real project.yaml.
        """
        sys.argv = ["pg-invoke-hook.py", "invoke-hook", *args]
        with mock.patch.object(self.mod, "find_project_root",
                               return_value=self.project_root_path), \
             mock.patch.object(self.mod, "_load_yaml") as mock_yaml, \
             mock.patch("builtins.open", mock.mock_open(
                 read_data=json.dumps(SAMPLE_CONFIG))), \
             mock.patch.object(self.mod, "subprocess") as mock_sub:
            mock_yaml.return_value.safe_load.return_value = SAMPLE_CONFIG
            mock_sub.run.side_effect = self._patched_run()
            return self.mod.invoke_hook_main(sys.argv)

    def test_prepare_env_accepted_without_role_or_instance(self):
        """`--action prepare_env` should be accepted without --role/--instance."""
        exit_code = self._call(
            "--change", "my-fix", "--env", "dev-local", "--action", "prepare_env",
        )
        self.assertEqual(exit_code, 0, "invoke-hook should exit 0 on success")
        spec = self.captured_spec
        self.assertIsNotNone(spec, "spec must be captured")
        self.assertEqual(spec["hook_type"], "prepare_env")
        self.assertEqual(spec["env"], "dev-local")
        self.assertEqual(spec["change"], "my-fix")
        self.assertEqual(spec["role"], "")
        self.assertEqual(spec["instance_name"], "")
        self.assertEqual(spec["instance_host"], "")
        # cmd should be: bash <script>  (no args for prepare_env)
        self.assertIn(".pg/hooks/env-dev-local-prepare.sh", spec["cmd"])
        self.assertTrue(spec["cmd"].startswith("bash "))
        # log_path is env-level (env.prepare_env.log)
        self.assertIn("env.prepare_env.log", spec["log_path"])
        self.assertEqual(spec["timeout_seconds"], 300)

    def test_clean_env_with_args_renders_correctly(self):
        """`--action clean_env` should render args from project.yaml."""
        exit_code = self._call(
            "--change", "my-fix", "--env", "dev-local", "--action", "clean_env",
        )
        self.assertEqual(exit_code, 0)
        spec = self.captured_spec
        self.assertEqual(spec["hook_type"], "clean_env")
        # clean_env has args: ["--force"]
        self.assertIn("--force", spec["cmd"])
        self.assertIn("env-dev-local-clean.sh", spec["cmd"])
        # log_path is env-level (env.clean_env.log)
        self.assertIn("env.clean_env.log", spec["log_path"])
        self.assertEqual(spec["timeout_seconds"], 120)

    def test_prepare_env_missing_in_env_fails(self):
        """If env has no prepare_env, invoke-hook should exit 1."""
        # We need to mock the config loading inside the module.
        sys.argv = ["pg-invoke-hook.py", "invoke-hook",
                    "--change", "x", "--env", "no-prepare",
                    "--action", "prepare_env"]
        config_without_prepare = {
            "environments": {
                "no-prepare": {
                    "roles": {},
                },
            },
        }
        with mock.patch.object(self.mod, "find_project_root",
                               return_value=self.project_root_path), \
             mock.patch.object(self.mod, "_load_yaml") as mock_yaml, \
             mock.patch("builtins.open", mock.mock_open(
                 read_data=json.dumps(config_without_prepare))):
            mock_yaml.return_value.safe_load.return_value = config_without_prepare
            rc = self.mod.invoke_hook_main(sys.argv)
            self.assertEqual(rc, 1)

    def test_role_action_still_works(self):
        """Per-role start action must still work after the env-level branch
        was added (regression check)."""
        self._call(
            "--change", "my-fix", "--env", "dev-local",
            "--role", "backend", "--instance", "backend-1",
            "--action", "start",
        )
        spec = self.captured_spec
        self.assertIsNotNone(spec)
        self.assertEqual(spec["hook_type"], "start")
        self.assertEqual(spec["role"], "backend")
        self.assertEqual(spec["instance_name"], "backend-1")
        # log_path uses role.*.start@*.log pattern
        self.assertIn("role.backend.start@backend-1.log", spec["log_path"])
        # cmd contains role/instance substitutions
        self.assertIn("backend", spec["cmd"])
        self.assertIn("backend-1", spec["cmd"])


class TestInvokeHookThinWrapper(unittest.TestCase):
    """Verify pg-pipeline-runner.py invoke-hook forwards to canonical executor.

    The thin wrapper must:
    - Accept the same CLI flags as the canonical executor
    - Exit code == pg-invoke-hook.py exit code
    - Subprocess call targets pg-invoke-hook.py
    """

    def setUp(self):
        self.wrapper = _load_wrapper()

    def test_wrapper_forwards_argv_to_canonical(self):
        """cmd_invoke_hook should subprocess.run(['python3', pg_invoke_hook_py, *argv[1:]])."""
        sys.argv = ["runner.py", "invoke-hook",
                    "--change", "x", "--env", "dev-local",
                    "--role", "backend", "--instance", "backend-1",
                    "--action", "start"]
        with mock.patch.object(self.wrapper, "subprocess") as mock_sub:
            mock_sub.run.return_value = mock.Mock(returncode=0)
            try:
                self.wrapper.cmd_invoke_hook(sys.argv)
            except SystemExit as e:
                self.assertEqual(e.code, 0)
            # Verify the subprocess.run was called with pg-invoke-hook.py
            call_args = mock_sub.run.call_args
            self.assertIsNotNone(call_args, "subprocess.run was not called")
            cmd = call_args.args[0] if call_args.args else call_args.kwargs.get("args")
            self.assertIsNotNone(cmd, "no args passed to subprocess.run")
            # cmd[0] = "python3", cmd[1] = pg-invoke-hook.py path
            self.assertEqual(cmd[0], "python3")
            self.assertTrue(cmd[1].endswith("pg-invoke-hook.py"),
                            f"cmd[1] should be pg-invoke-hook.py path, got {cmd[1]!r}")
            # argv[1:] (excluding program name) should be passed through
            # i.e., "invoke-hook" + flags
            self.assertIn("invoke-hook", cmd[2:])

    def test_wrapper_returns_subprocess_exit_code(self):
        """cmd_invoke_hook should sys.exit(proc.returncode)."""
        sys.argv = ["runner.py", "invoke-hook",
                    "--change", "x", "--env", "dev-local",
                    "--action", "prepare_env"]
        with mock.patch.object(self.wrapper, "subprocess") as mock_sub:
            mock_sub.run.return_value = mock.Mock(returncode=42)
            with self.assertRaises(SystemExit) as cm:
                self.wrapper.cmd_invoke_hook(sys.argv)
            self.assertEqual(cm.exception.code, 42)


if __name__ == "__main__":
    unittest.main()
