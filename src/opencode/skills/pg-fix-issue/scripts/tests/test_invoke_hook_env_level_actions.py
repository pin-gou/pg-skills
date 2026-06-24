"""Tests for invoke-hook CLI supporting environment-level actions.

Validates the v3.0 expansion of `pg-pipeline-runner.py invoke-hook`:
- `--action prepare_env` / `--action clean_env` are accepted
- These actions skip role/instance validation
- The spec built for env-level actions has empty role/instance_host
  but well-formed cmd/log_path/hook_type
- Args from `environments.<env>.prepare_env.args` are rendered into cmd

Strategy: drive cmd_invoke_hook directly with patched sys.argv + load_config,
capturing the spec passed to pg-run-hook.py (we mock subprocess.run so no
real hook execution happens).
"""

import json
import os
import sys
import unittest
from unittest import mock

# Locate the runner module. The runner lives at:
#   .opencode/skills/pg-build/scripts/pg-pipeline-runner.py
# (a real file, not symlinked).
HERE = os.path.realpath(os.path.abspath(__file__))


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


PROJECT_ROOT = _find_project_root(HERE)
RUNNER_PATH = os.path.join(
    PROJECT_ROOT,
    ".opencode", "skills", "pg-build", "scripts", "pg-pipeline-runner.py",
)


def _load_runner():
    """Import the runner module from absolute path.

    runner.py imports sibling helpers (pg_context_chain, pg_pipeline_common,
    pg_pipeline_state) at module top level. We must add the scripts dir
    to sys.path before importing.
    """
    import importlib.util
    import sys
    scripts_dir = os.path.dirname(RUNNER_PATH)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("pg_pipeline_runner", RUNNER_PATH)
    assert spec is not None, f"Cannot load spec for {RUNNER_PATH}"
    assert spec.loader is not None, "loader is None"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
    """invoke-hook must accept prepare_env / clean_env as actions."""

    def setUp(self):
        self.runner = _load_runner()
        self.captured_spec = None

    def _patched_run(self, returncode=0):
        """Return a side_effect for subprocess.run that captures the spec.

        runner.cmd_invoke_hook pipes the spec as JSON to subprocess.run's
        stdin. We patch subprocess.run inside the runner module so we can
        inspect the spec without invoking pg-run-hook.py.

        The returned object MUST have a real `.returncode` attribute (an int),
        because cmd_invoke_hook does sys.exit(proc.returncode).
        """
        def fake_run(argv, input=None, **kwargs):
            self.captured_spec = json.loads(input)
            return mock.Mock(returncode=returncode)

        return fake_run

    def _call(self, *args):
        """Invoke cmd_invoke_hook with sys.argv = ["runner.py", "invoke-hook", *args].

        Returns the SystemExit raised by cmd_invoke_hook (so tests can
        assert exit codes).
        """
        sys.argv = ["pg-pipeline-runner.py", "invoke-hook", *args]
        with mock.patch.object(self.runner, "load_config",
                               return_value=SAMPLE_CONFIG), \
             mock.patch.object(self.runner, "subprocess") as mock_sub:
            mock_sub.run.side_effect = self._patched_run()
            try:
                self.runner.cmd_invoke_hook(sys.argv)
            except SystemExit as e:
                return e.code
        return 0

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
        config_without_prepare = {
            "environments": {
                "no-prepare": {
                    "roles": {},  # no prepare_env either
                },
            },
        }
        sys.argv = ["pg-pipeline-runner.py", "invoke-hook",
                    "--change", "x", "--env", "no-prepare",
                    "--action", "prepare_env"]
        with mock.patch.object(self.runner, "load_config",
                               return_value=config_without_prepare):
            with self.assertRaises(SystemExit) as cm:
                self.runner.cmd_invoke_hook(sys.argv)
            self.assertEqual(cm.exception.code, 1)

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


if __name__ == "__main__":
    unittest.main()