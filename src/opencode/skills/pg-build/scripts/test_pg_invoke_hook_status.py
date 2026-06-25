"""Tests for pg-invoke-hook.py status subcommand.

The status subcommand is an LLM-facing passthrough to
pg-pipeline-runner.py prepare-env-status. Validates:
1. Top-level dispatch routes 'status' to status_main (not invoke_hook_main)
2. status_main forwards to pg-pipeline-runner.py with correct argv
3. status_main forwards exit code from runner
4. status_main forwards stdout/stderr from runner
5. --stage flag is appended positionally (not as --stage flag)
6. Missing --change exits 2 (argparse)
7. Unknown subcommand exits 2 (dispatcher)
8. --help works
9. Backward compat: bare `pg-invoke-hook.py <flags>` defaults to invoke-hook
"""
import importlib.util
import io
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock


_HERE = os.path.dirname(os.path.abspath(__file__))
# _HERE points to e.g. .opencode/skills/pg-build/scripts/ (from oc2-web-virt)
# or .pg/skills/src/opencode/skills/pg-build/scripts/ (from the canonical path),
# OR /home/ubuntu/workspace/pg-skills/src/opencode/skills/pg-build/scripts/
# (the upstream pg-skills git checkout — hardlinked to oc2-web-virt, but has
# NO .pg/project.yaml so it's not the project root).
#
# Resolution order:
#   1. PG_PROJECT_ROOT env var (test runners may set this)
#   2. Walk up from _HERE looking for .pg/project.yaml
#   3. Walk up from cwd (handles the case where _HERE was hardlink-resolved
#      to upstream pg-skills — cwd may still be the real project root)


def _find_project_root():
    env_root = os.environ.get("PG_PROJECT_ROOT")
    if env_root and os.path.isfile(os.path.join(env_root, ".pg", "project.yaml")):
        return env_root

    candidates = [_HERE, os.getcwd()]
    seen = set()
    for start in candidates:
        p = start
        for _ in range(10):
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
        f"Cannot find .pg/project.yaml. _HERE={_HERE!r}, cwd={os.getcwd()!r}. "
        f"Set PG_PROJECT_ROOT env var to the oc2-web-virt directory."
    )


PROJECT_ROOT = _find_project_root()
PG_INVOKE_HOOK_PATH = os.path.join(
    PROJECT_ROOT, ".pg", "skills", "src", "runtime", "bin", "pg-invoke-hook.py"
)


def _load_pg_invoke_hook():
    """Load pg-invoke-hook.py module by file path (handles hardlinked paths)."""
    spec = importlib.util.spec_from_file_location("pg_invoke_hook", PG_INVOKE_HOOK_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load pg-invoke-hook.py from {PG_INVOKE_HOOK_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PG_INVOKE_HOOK = _load_pg_invoke_hook()


def _run_cli(*args, timeout=30):
    """Invoke `pg-invoke-hook.py` as a subprocess (real CLI)."""
    return subprocess.run(
        ["python3", PG_INVOKE_HOOK_PATH, *args],
        capture_output=True, text=True, timeout=timeout,
        cwd=PROJECT_ROOT,
    )


class TestStatusDispatcher(unittest.TestCase):
    """Top-level dispatcher correctly routes 'status' vs 'invoke-hook'."""

    def test_status_subcommand_routes_to_status_main(self):
        """When argv[1] == 'status', main() must dispatch to status_main,
        not invoke_hook_main."""
        with mock.patch.object(PG_INVOKE_HOOK, "status_main",
                               return_value=42) as mock_status, \
             mock.patch.object(PG_INVOKE_HOOK, "invoke_hook_main",
                               return_value=99) as mock_invoke:
            rc = PG_INVOKE_HOOK.main(
                ["pg-invoke-hook.py", "status", "--change", "C"]
            )
            self.assertEqual(rc, 42, "status subcommand must route to status_main")
            mock_status.assert_called_once()
            mock_invoke.assert_not_called()

    def test_invoke_hook_subcommand_routes_to_invoke_hook_main(self):
        """When argv[1] == 'invoke-hook', main() must dispatch to invoke_hook_main."""
        with mock.patch.object(PG_INVOKE_HOOK, "invoke_hook_main",
                               return_value=77) as mock_invoke, \
             mock.patch.object(PG_INVOKE_HOOK, "status_main",
                               return_value=99) as mock_status:
            rc = PG_INVOKE_HOOK.main(
                ["pg-invoke-hook.py", "invoke-hook",
                 "--change", "C", "--env", "E", "--role", "R",
                 "--instance", "I", "--action", "start"]
            )
            self.assertEqual(rc, 77, "invoke-hook subcommand must route to invoke_hook_main")
            mock_invoke.assert_called_once()
            mock_status.assert_not_called()

    def test_bare_flag_form_defaults_to_invoke_hook(self):
        """Backward compat: `pg-invoke-hook.py --change X ...` (no subcommand)
        must default to invoke-hook for SKILL.md prompts using the no-subcommand form."""
        with mock.patch.object(PG_INVOKE_HOOK, "invoke_hook_main",
                               return_value=33) as mock_invoke, \
             mock.patch.object(PG_INVOKE_HOOK, "status_main",
                               return_value=99) as mock_status:
            rc = PG_INVOKE_HOOK.main(
                ["pg-invoke-hook.py", "--change", "C", "--env", "E", "--action", "start"]
            )
            self.assertEqual(rc, 33, "flag form must route to invoke_hook_main")
            mock_invoke.assert_called_once()
            mock_status.assert_not_called()

    def test_unknown_subcommand_exits_2(self):
        """Unknown subcommand returns 2 with usage hint on stderr."""
        with redirect_stdout(io.StringIO()) as out, \
             redirect_stderr(io.StringIO()) as err:
            rc = PG_INVOKE_HOOK.main(["pg-invoke-hook.py", "unknown-sub"])
        self.assertEqual(rc, 2)
        self.assertIn("unknown subcommand", err.getvalue())
        self.assertIn("invoke-hook", err.getvalue())
        self.assertIn("status", err.getvalue())

    def test_no_args_prints_usage_and_exits_2(self):
        """Bare invocation (no args) prints usage to stderr and exits 2."""
        with redirect_stdout(io.StringIO()) as out, \
             redirect_stderr(io.StringIO()) as err:
            rc = PG_INVOKE_HOOK.main(["pg-invoke-hook.py"])
        self.assertEqual(rc, 2)
        self.assertIn("Usage", err.getvalue())


class TestStatusMainArgparse(unittest.TestCase):
    """status_main() argument parsing."""

    def test_missing_change_exits_2(self):
        """status_main without --change exits 2 (argparse error)."""
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                PG_INVOKE_HOOK.status_main(
                    ["pg-invoke-hook.py", "status"]
                )
        self.assertEqual(ctx.exception.code, 2)

    def test_help_works(self):
        """--help exits 0 (argparse convention) and shows usage."""
        with redirect_stdout(io.StringIO()) as out, \
             redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as ctx:
                PG_INVOKE_HOOK.status_main(
                    ["pg-invoke-hook.py", "status", "--help"]
                )
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("prepare-env-status", out.getvalue())


class TestStatusPassthrough(unittest.TestCase):
    """status_main forwards to pg-pipeline-runner.py prepare-env-status."""

    def test_status_forwards_change_positionally(self):
        """status_main spawns runner with prepare-env-status <change> (positional argv)."""
        with mock.patch.object(PG_INVOKE_HOOK.subprocess, "run") as mock_run:
            mock_proc = mock.Mock()
            mock_proc.returncode = 0
            mock_run.return_value = mock_proc

            rc = PG_INVOKE_HOOK.status_main(
                ["pg-invoke-hook.py", "status", "--change", "my-change"]
            )

            self.assertEqual(rc, 0)
            mock_run.assert_called_once()
            cmd = mock_run.call_args.args[0]
            self.assertEqual(cmd[0], "python3")
            self.assertTrue(cmd[1].endswith("pg-pipeline-runner.py"),
                            f"runner path expected, got {cmd[1]!r}")
            self.assertEqual(cmd[2], "prepare-env-status")
            self.assertEqual(cmd[3], "my-change",
                             "--change must become positional arg to runner")
            self.assertEqual(len(cmd), 4,
                             "no --stage means no positional stage arg")

    def test_status_with_stage_appends_positionally(self):
        """--stage is appended as positional arg (NOT as --stage flag to runner)."""
        with mock.patch.object(PG_INVOKE_HOOK.subprocess, "run") as mock_run:
            mock_proc = mock.Mock()
            mock_proc.returncode = 0
            mock_run.return_value = mock_proc

            rc = PG_INVOKE_HOOK.status_main(
                ["pg-invoke-hook.py", "status",
                 "--change", "my-change", "--stage", "dev-backend"]
            )

            self.assertEqual(rc, 0)
            cmd = mock_run.call_args.args[0]
            self.assertEqual(cmd[3], "my-change")
            self.assertEqual(cmd[4], "dev-backend",
                             "--stage must be appended positionally, not as --stage flag")
            self.assertEqual(len(cmd), 5)
            self.assertNotIn("--stage", cmd,
                             "runner expects positional stage, not --stage flag")

    def test_status_forwards_runner_exit_code(self):
        """If runner exits 1, status_main returns 1; if 7, returns 7."""
        for runner_rc in (0, 1, 7, 42, 99):
            with mock.patch.object(PG_INVOKE_HOOK.subprocess, "run") as mock_run:
                mock_proc = mock.Mock()
                mock_proc.returncode = runner_rc
                mock_run.return_value = mock_proc

                rc = PG_INVOKE_HOOK.status_main(
                    ["pg-invoke-hook.py", "status", "--change", "X"]
                )
                self.assertEqual(
                    rc, runner_rc,
                    f"runner exit {runner_rc} must propagate as-is"
                )

    def test_status_forwards_cwd(self):
        """status_main runs subprocess in project root (so relative paths work)."""
        with mock.patch.object(PG_INVOKE_HOOK.subprocess, "run") as mock_run:
            mock_proc = mock.Mock()
            mock_proc.returncode = 0
            mock_run.return_value = mock_proc

            PG_INVOKE_HOOK.status_main(
                ["pg-invoke-hook.py", "status", "--change", "X"]
            )

            kwargs = mock_run.call_args.kwargs
            self.assertIn("cwd", kwargs, "subprocess.run must receive cwd kwarg")
            # cwd should be the project root (where .pg/project.yaml lives)
            self.assertTrue(kwargs["cwd"].endswith("oc2-web-virt") or
                            os.path.isfile(os.path.join(kwargs["cwd"], ".pg", "project.yaml")),
                            f"cwd must be project root, got {kwargs['cwd']!r}")


class TestStatusRealCli(unittest.TestCase):
    """Real subprocess integration tests (end-to-end CLI)."""

    def test_status_help_via_cli(self):
        """Real `pg-invoke-hook.py status --help` exits 0."""
        proc = _run_cli("status", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("prepare-env-status", proc.stdout)

    def test_status_missing_change_via_cli(self):
        """Real `pg-invoke-hook.py status` (no --change) exits 2."""
        proc = _run_cli("status")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--change", proc.stderr)

    def test_status_dispatches_to_runner_returns_json(self):
        """Real status call returns JSON from runner (prepare_env records exist or not)."""
        proc = _run_cli("status", "--change", "e2e-check")
        self.assertEqual(proc.returncode, 0, f"stderr: {proc.stderr}")
        # runner outputs JSON array; verify it parses
        import json as _json
        data = _json.loads(proc.stdout)
        self.assertIsInstance(data, list)
        for entry in data:
            self.assertIn("stage", entry)
            self.assertIn("prepare", entry)


if __name__ == "__main__":
    unittest.main()