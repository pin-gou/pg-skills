#!/usr/bin/env python3
"""Tests for pg_build_bootstrap / pg_build_dispatch_context / pg_build_record_log.

These helpers are extracted from v1 cmd_next / cmd_record to be reused by
v2 cmd_next_v2 / cmd_record_v2. v1 should behave identically; v2 should
finally emit the side effects that were lost during build-r Step 3 (the
state_v2 migration).

Coverage:
- pg_build_bootstrap:
    * Idempotency (second call returns None when init_committed=True)
    * Calls all 4 sub-functions in the right order
    * Accepts v1 state dict
    * Accepts v2 PipelineState (type-dispatch + _persist_state_mutation)
    * Failure is non-fatal (init_commit failure does not raise)
- pg_build_dispatch_context:
    * Returns (ctx, has_rollback) tuple
    * ctx contains _change, tasks_preformatted, prompt_injection, stage
    * rollback_context is populated when rollback_get returns found=True
    * has_rollback is True only when rollback_context is populated
    * Failure in any enrich function does not raise
- pg_build_record_log:
    * Each (sub, status) pair calls the right pg_context_chain methods
    * Best-effort: import failure / dispatch failure do not raise
- _normalize_state_for_bootstrap / _persist_state_mutation:
    * v1 dict is returned as-is
    * v2 PipelineState is converted to .data["context"] dict
    * State mutation persists back to v2 PipelineState via .commit()
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _load_module(name, path):
    """Import a module fresh from path, ensuring module-level state is clean."""
    if name in sys.modules:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class _HelpersTestBase(unittest.TestCase):
    """Common scaffolding: temp git repo with proposal artifacts at change root."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test-pg-build-helpers-")
        self.pg_spec = os.path.join(self.tmpdir, "pg-spec")
        self.changes_dir = os.path.join(self.pg_spec, "changes")
        self.change = "test-change"
        self.change_dir = os.path.join(self.changes_dir, self.change)
        self.apply_dir = os.path.join(self.change_dir, "2-build")
        os.makedirs(self.change_dir)

        # Minimal v3.0 config so load_config works
        with open(os.path.join(self.pg_spec, "config.yaml"), "w") as f:
            f.write(
                "schema: spec-driven\n"
                "modules:\n"
                "  dummy-mod:\n"
                "    root: /tmp\n"
                "    language: python\n"
                "    test:\n"
                "      unit: 'true'\n"
                "tracks:\n"
                "  dummy-track:\n"
                "    modules: [dummy-mod]\n"
                "    max_fix_retries: 5\n"
                "    fix_routing: source\n"
                "    review_level: none\n"
                "    label: dummy\n"
                "stages:\n"
                "  - name: dev-isolated\n"
                "    tracks: [dummy-track]\n"
                "    test_key: unit\n"
                "    environment:\n"
                "      required: false\n"
                "    gate: all_pass\n"
            )

        for name in ("proposal.md", "design.md", "tasks.md"):
            with open(os.path.join(self.change_dir, name), "w") as f:
                f.write(f"# {name}\n")
        with open(os.path.join(self.change_dir, "tasks.md"), "w") as f:
            f.write(
                "# Tasks\n\n"
                "## 1. dummy-track:test - dummy test\n\n"
                "- [ ] 1.1 do something\n\n"
            )

        _git(self.tmpdir, "init", "-q", "-b", "master")
        _git(self.tmpdir, "config", "user.email", "t@t")
        _git(self.tmpdir, "config", "user.name", "t")
        _git(self.tmpdir, "commit", "--allow-empty", "-q", "-m", "empty baseline")

        self.runner = _load_module("pg_pipeline_runner",
                                   os.path.join(SCRIPTS_DIR, "pg-pipeline-runner.py"))
        setattr(self.runner, "PROJECT_ROOT", self.tmpdir)
        setattr(self.runner, "CHANGES_DIR", self.changes_dir)

        self.common = _load_module("pg_pipeline_common",
                                   os.path.join(SCRIPTS_DIR, "pg_pipeline_common.py"))

    def tearDown(self):
        for name in ("pg_pipeline_runner", "pg_pipeline_common", "pg_context_chain"):
            if name in sys.modules:
                del sys.modules[name]
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestNormalizeState(_HelpersTestBase):
    """Unit tests for the internal state type-dispatch helpers."""

    def test_normalize_v1_dict(self):
        state = {"init_committed": False, "current": None}
        out = self.common._normalize_state_for_bootstrap(state)
        self.assertIs(out, state)
        self.assertEqual(out["init_committed"], False)

    def test_normalize_v2_pipeline_state(self):
        class FakePS:
            def __init__(self):
                self.data = {"context": {"init_committed": False}}
        out = self.common._normalize_state_for_bootstrap(FakePS())
        self.assertEqual(out["init_committed"], False)

    def test_normalize_v2_missing_context(self):
        class FakePS:
            data = {}  # no "context" key
        out = self.common._normalize_state_for_bootstrap(FakePS())
        self.assertEqual(out, {})

    def test_normalize_raises_on_unsupported(self):
        with self.assertRaises(TypeError):
            self.common._normalize_state_for_bootstrap("not a state")

    def test_persist_mutation_writes_back_to_v2(self):
        class FakePS:
            def __init__(self):
                self.data = {"context": {"init_committed": False}}
                self.committed = 0
            def commit(self):
                self.committed += 1
        ps = FakePS()
        self.common._persist_state_mutation(ps, "init_committed", True)
        self.assertEqual(ps.data["context"]["init_committed"], True)
        self.assertEqual(ps.committed, 1)

    def test_persist_mutation_writes_to_v1_dict(self):
        state = {"init_committed": False}
        self.common._persist_state_mutation(state, "init_committed", True)
        self.assertEqual(state["init_committed"], True)
        # Note: caller is responsible for save_state() to disk; this helper
        # only mutates the in-memory dict.


class TestPgBuildBootstrap(_HelpersTestBase):
    """Tests for pg_build_bootstrap."""

    def test_returns_init_commit_dict_on_first_call(self):
        state = {"init_committed": False}
        with mock.patch.object(self.runner, "_ensure_context_chain"), \
             mock.patch.object(self.runner, "migrate_legacy_state_files", return_value=[]), \
             mock.patch.object(self.runner, "_maybe_bootstrap_init_commit",
                               return_value={"branch": f"feat/pg/{self.change}",
                                             "sha": "abc1234",
                                             "message": f"chore({self.change}): bootstrap pg-build",
                                             "committed": True}):
            result = self.common.pg_build_bootstrap(self.change, state)
        self.assertIsNotNone(result)
        self.assertEqual(result["branch"], f"feat/pg/{self.change}")
        self.assertEqual(result["committed"], True)
        self.assertEqual(state["init_committed"], True)

    def test_returns_none_on_second_call_when_already_committed(self):
        state = {"init_committed": True}
        with mock.patch.object(self.runner, "_ensure_context_chain") as mock_chain, \
             mock.patch.object(self.runner, "migrate_legacy_state_files", return_value=[]), \
             mock.patch.object(self.runner, "_maybe_bootstrap_init_commit",
                               return_value=None) as mock_init:
            result = self.common.pg_build_bootstrap(self.change, state)
        # v1 _maybe_bootstrap_init_commit itself short-circuits on init_committed=True
        # (returning None), so the mock IS called but returns None.
        # Our helper then does not call _persist_state_mutation (init_commit is None).
        self.assertIsNone(result)
        mock_chain.assert_called_once_with(self.change)
        mock_init.assert_called_once_with(self.change, state)

    def test_calls_all_four_sub_functions_in_order(self):
        state = {"init_committed": False}
        call_order = []
        with mock.patch.object(self.runner, "migrate_legacy_state_files",
                               side_effect=lambda c: (call_order.append("migrate"), [])[1]), \
             mock.patch.object(self.runner, "_ensure_context_chain",
                               side_effect=lambda c: call_order.append("context_chain")), \
             mock.patch.object(self.runner, "_ensure_feature_branch",
                               side_effect=lambda c: call_order.append("feature_branch")), \
             mock.patch.object(self.runner, "_maybe_bootstrap_init_commit",
                               side_effect=lambda c, s: (call_order.append("init_commit"), None)[1]):
            self.common.pg_build_bootstrap(self.change, state)
        self.assertEqual(call_order, ["migrate", "context_chain", "feature_branch", "init_commit"])

    def test_accepts_v2_pipeline_state_and_persists_marker(self):
        class FakePS:
            def __init__(self):
                self.data = {"context": {"init_committed": False}}
                self.committed = 0
            def commit(self):
                self.committed += 1
        ps = FakePS()
        with mock.patch.object(self.runner, "_ensure_context_chain"), \
             mock.patch.object(self.runner, "migrate_legacy_state_files", return_value=[]), \
             mock.patch.object(self.runner, "_maybe_bootstrap_init_commit",
                               return_value={"branch": f"feat/pg/{self.change}", "committed": True}), \
             mock.patch.object(self.common, "execute_env_hook_inline",
                               return_value={"success": True, "skipped": True,
                                             "log_path": None, "exit_code": None,
                                             "env_name": None, "phase_item": None}):
            self.common.pg_build_bootstrap(self.change, ps)
        self.assertEqual(ps.data["context"]["init_committed"], True)
        # v2 enhancement: pg_build_bootstrap now also commits when env-hook
        # completes (write state.context.prepare_env_completed marker), so
        # the commit count is 2 not 1.
        self.assertGreaterEqual(ps.committed, 1)

    def test_failure_in_sub_function_does_not_raise(self):
        state = {"init_committed": False}
        with mock.patch.object(self.runner, "_ensure_context_chain",
                               side_effect=RuntimeError("boom")), \
             mock.patch.object(self.runner, "migrate_legacy_state_files", return_value=[]), \
             mock.patch.object(self.runner, "_maybe_bootstrap_init_commit",
                               return_value={"committed": True}):
            result = self.common.pg_build_bootstrap(self.change, state)
        self.assertIsNotNone(result)
        self.assertEqual(state["init_committed"], True)

    def test_init_commit_failure_still_returns_none(self):
        """If _maybe_bootstrap_init_commit raises, pg_build_bootstrap must swallow
        the exception, return None, and not propagate. The real v1 runner mutates
        state['init_committed']=True inside the helper before calling commit,
        so even on commit failure state ends up True (marker is set to skip retry).
        Our mock doesn't replicate that side effect; the key invariant we test is
        'no exception propagates'."""
        state = {"init_committed": False}
        with mock.patch.object(self.runner, "_ensure_context_chain"), \
             mock.patch.object(self.runner, "migrate_legacy_state_files", return_value=[]), \
             mock.patch.object(self.runner, "_maybe_bootstrap_init_commit",
                               side_effect=RuntimeError("git failed")):
            result = self.common.pg_build_bootstrap(self.change, state)
        self.assertIsNone(result)


class TestPgBuildDispatchContext(_HelpersTestBase):
    """Tests for pg_build_dispatch_context."""

    def _fake_config(self):
        # We don't need a real config — pass a minimal dict.
        return {"modules": {}, "tracks": {}, "stages": [], "build_rules": []}

    def test_returns_ctx_and_has_rollback_tuple(self):
        with mock.patch.object(self.runner, "pg_context_chain") as mock_chain:
            mock_chain.rollback_get = mock.MagicMock(return_value={"found": False})
            ctx, has_rollback = self.common.pg_build_dispatch_context(
                self.change, "dummy-track", "test", self._fake_config())
        self.assertIsInstance(ctx, dict)
        self.assertEqual(ctx["_change"], self.change)
        self.assertFalse(has_rollback)

    def test_ctx_contains_change_key(self):
        with mock.patch.object(self.runner, "pg_context_chain") as mock_chain:
            mock_chain.rollback_get = mock.MagicMock(return_value={"found": False})
            ctx, _ = self.common.pg_build_dispatch_context(
                self.change, "dummy-track", "test", self._fake_config())
        self.assertEqual(ctx["_change"], self.change)

    def test_rollback_context_populated_when_found(self):
        with mock.patch.object(self.runner, "pg_context_chain") as mock_chain:
            mock_chain.rollback_get = mock.MagicMock(return_value={
                "found": True, "failed_at": "2026-01-01", "reason": "x failed", "source": "verify"})
            ctx, has_rollback = self.common.pg_build_dispatch_context(
                self.change, "dummy-track", "fix", self._fake_config())
        self.assertTrue(has_rollback)
        self.assertIn("rollback_context", ctx)
        self.assertEqual(ctx["rollback_context"]["failed_at"], "2026-01-01")

    def test_rollback_not_populated_when_not_found(self):
        with mock.patch.object(self.runner, "pg_context_chain") as mock_chain:
            mock_chain.rollback_get = mock.MagicMock(return_value={"found": False})
            ctx, has_rollback = self.common.pg_build_dispatch_context(
                self.change, "dummy-track", "fix", self._fake_config())
        self.assertFalse(has_rollback)
        self.assertNotIn("rollback_context", ctx)

    def test_enrich_failure_does_not_raise(self):
        with mock.patch.object(self.runner, "pg_context_chain") as mock_chain:
            mock_chain.rollback_get = mock.MagicMock(side_effect=RuntimeError("chain broken"))
            ctx, has_rollback = self.common.pg_build_dispatch_context(
                self.change, "dummy-track", "test", self._fake_config())
        self.assertIsInstance(ctx, dict)
        self.assertFalse(has_rollback)


class TestPgBuildRecordLog(_HelpersTestBase):
    """Tests for pg_build_record_log (v2 path context-chain logging)."""

    def test_completed_test_logs_sub_end(self):
        with mock.patch.object(self.common, "pg_context_chain", create=True) as mock_chain:
            # Patch the locally-imported reference inside the helper via sys.modules
            with mock.patch.dict(sys.modules, {"pg_context_chain": mock_chain}):
                mock_chain.sub_end = mock.MagicMock()
                self.common.pg_build_record_log(
                    self.change, "backend", "test", "completed",
                    summary="done", outputs="1.1", issues="")
        mock_chain.sub_end.assert_called_once()
        args = mock_chain.sub_end.call_args[0]
        self.assertEqual(args[0], self.change)
        self.assertEqual(args[1], "backend")
        self.assertEqual(args[2], "test")
        self.assertEqual(args[3], "COMPLETED")

    def test_completed_fix_logs_sub_end_and_sub_start(self):
        with mock.patch.dict(sys.modules, {"pg_context_chain": mock.MagicMock()}) as mp:
            mock_chain = mp["pg_context_chain"]
            mock_chain.sub_end = mock.MagicMock()
            mock_chain.sub_start = mock.MagicMock()
            self.common.pg_build_record_log(
                self.change, "backend", "fix", "completed",
                summary="fixed", outputs="1.1", issues="")
        mock_chain.sub_end.assert_called_once()
        mock_chain.sub_start.assert_called_once()
        # sub_start signature: sub_start(change, item, phase, fix_cycle=1)
        # args[0]=change, args[1]=item, args[2]=phase
        sub_start_args = mock_chain.sub_start.call_args[0]
        self.assertEqual(sub_start_args[0], self.change)
        self.assertEqual(sub_start_args[1], "backend")
        self.assertEqual(sub_start_args[2], "verify")

    def test_failed_logs_sub_end_with_issues(self):
        with mock.patch.dict(sys.modules, {"pg_context_chain": mock.MagicMock()}) as mp:
            mock_chain = mp["pg_context_chain"]
            mock_chain.sub_end = mock.MagicMock()
            self.common.pg_build_record_log(
                self.change, "backend", "test", "failed",
                summary="", outputs="", issues="compile error")
        mock_chain.sub_end.assert_called_once()
        args = mock_chain.sub_end.call_args[0]
        self.assertEqual(args[3], "FAILED")
        # issues is the last positional arg
        self.assertIn("compile error", mock_chain.sub_end.call_args[0])

    def test_escalate_logs_sub_end_then_sub_start_fix(self):
        with mock.patch.dict(sys.modules, {"pg_context_chain": mock.MagicMock()}) as mp:
            mock_chain = mp["pg_context_chain"]
            mock_chain.sub_end = mock.MagicMock()
            mock_chain.sub_start = mock.MagicMock()
            self.common.pg_build_record_log(
                self.change, "backend", "verify", "escalate",
                summary="needs fix", outputs="", issues="")
        mock_chain.sub_end.assert_called_once()
        mock_chain.sub_start.assert_called_once_with(
            self.change, "backend", "fix", fix_cycle=1)

    def test_pass_track_logs_sub_end_pass(self):
        with mock.patch.dict(sys.modules, {"pg_context_chain": mock.MagicMock()}) as mp:
            mock_chain = mp["pg_context_chain"]
            mock_chain.sub_end = mock.MagicMock()
            self.common.pg_build_record_log(
                self.change, "backend", "gate", "pass",
                summary="gate ok", outputs="", issues="")
        mock_chain.sub_end.assert_called_once()
        args = mock_chain.sub_end.call_args[0]
        self.assertEqual(args[3], "PASS")

    def test_pass_final_gate_logs_sub_end(self):
        with mock.patch.dict(sys.modules, {"pg_context_chain": mock.MagicMock()}) as mp:
            mock_chain = mp["pg_context_chain"]
            mock_chain.sub_end = mock.MagicMock()
            self.common.pg_build_record_log(
                self.change, "final-gate", None, "pass",
                summary="all green", outputs="", issues="")
        mock_chain.sub_end.assert_called_once()
        args = mock_chain.sub_end.call_args[0]
        self.assertEqual(args[1], "final-gate")
        self.assertEqual(args[3], "PASS")

    def test_fail_track_logs_sub_end_fail_then_rollback_set(self):
        with mock.patch.dict(sys.modules, {"pg_context_chain": mock.MagicMock()}) as mp:
            mock_chain = mp["pg_context_chain"]
            mock_chain.sub_end = mock.MagicMock()
            mock_chain.rollback_set = mock.MagicMock()
            mock_chain.sub_start = mock.MagicMock()
            self.common.pg_build_record_log(
                self.change, "backend", "gate", "fail",
                summary="gap G-1", outputs="", issues="")
        mock_chain.sub_end.assert_called_once()
        mock_chain.rollback_set.assert_called_once()
        mock_chain.sub_start.assert_called_once()
        # sub_start signature: sub_start(change, item, phase, fix_cycle=1)
        # args[0]=change, args[1]=item, args[2]=phase
        sub_start_args = mock_chain.sub_start.call_args[0]
        self.assertEqual(sub_start_args[0], self.change)
        self.assertEqual(sub_start_args[1], "backend")
        self.assertEqual(sub_start_args[2], "fix-gate")

    def test_fail_final_gate_does_nothing(self):
        with mock.patch.dict(sys.modules, {"pg_context_chain": mock.MagicMock()}) as mp:
            mock_chain = mp["pg_context_chain"]
            mock_chain.sub_end = mock.MagicMock()
            self.common.pg_build_record_log(
                self.change, "final-gate", None, "fail",
                summary="", outputs="", issues="")
        mock_chain.sub_end.assert_not_called()

    def test_import_failure_does_not_raise(self):
        # Force pg_context_chain import to fail
        with mock.patch.dict(sys.modules, {"pg_context_chain": None}):
            # Should not raise even though pg_context_chain import fails
            try:
                self.common.pg_build_record_log(
                    self.change, "backend", "test", "completed")
            except Exception as e:
                self.fail(f"pg_build_record_log raised on import failure: {e}")


class TestEnvHookError(_HelpersTestBase):
    """Tests for the EnvHookError exception class."""

    def test_carries_phase_name_log_path_exit_code(self):
        from pg_pipeline_common import EnvHookError
        e = EnvHookError("prepare_env", "/tmp/x.log", 2)
        self.assertEqual(e.phase_name, "prepare_env")
        self.assertEqual(e.log_path, "/tmp/x.log")
        self.assertEqual(e.exit_code, 2)
        # __str__ should mention all three.
        msg = str(e)
        self.assertIn("prepare_env", msg)
        self.assertIn("/tmp/x.log", msg)
        self.assertIn("2", msg)


class TestExecuteEnvHookInline(_HelpersTestBase):
    """Tests for execute_env_hook_inline (env-hook inline execution).

    Covers P0/P1: env-hook must run synchronously during bootstrap, log
    phase_start/phase_end to context-chain.md, return a result dict, and
    raise EnvHookError on failure (caller surfaces as env_hook_failed).
    """

    def test_invalid_phase_name_returns_error(self):
        result = self.common.execute_env_hook_inline(self.change, "bogus_phase")
        self.assertFalse(result["success"])
        self.assertIn("invalid phase_name", result["error"])

    def test_no_stages_returns_skipped(self):
        # config has stages=[] → no first stage to resolve
        with mock.patch.object(self.runner, "load_config",
                               return_value={"stages": [], "environments": {}}):
            result = self.common.execute_env_hook_inline(self.change, "prepare_env")
        self.assertTrue(result["success"])
        self.assertTrue(result["skipped"])

    def test_skipped_when_no_hook_configured(self):
        # env_name resolves to something but env_cfg.prepare_env is empty
        with mock.patch.object(self.runner, "load_config",
                               return_value={
                                   "stages": [{"name": "dev", "tracks": ["dummy-track"]}],
                                   "environments": {"dev-local": {}}
                               }), \
             mock.patch.object(self.runner, "_resolve_stage_env",
                               return_value="dev-local"):
            result = self.common.execute_env_hook_inline(self.change, "prepare_env")
        self.assertTrue(result["success"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["env_name"], "dev-local")

    def test_skipped_when_stage_env_is_skip(self):
        # _resolve_stage_env returns __skip__ → user disabled this stage's env
        with mock.patch.object(self.runner, "load_config",
                               return_value={
                                   "stages": [{"name": "dev", "tracks": ["dummy-track"]}],
                                   "environments": {}
                               }), \
             mock.patch.object(self.runner, "_resolve_stage_env",
                               return_value="__skip__"):
            result = self.common.execute_env_hook_inline(self.change, "prepare_env")
        self.assertTrue(result["success"])
        self.assertTrue(result["skipped"])

    def test_success_path_runs_script_and_logs_phases(self):
        # Mock everything: hook script is a no-op `true` that exits 0.
        # Mock _phase_log_path to return a tmp path under our test dir.
        # Mock _subprocess.run to short-circuit the actual pg-run-hook
        # call (which doesn't exist in the test env).
        log_path = os.path.join(self.tmpdir, "hook.log")
        fake_proc_success = mock.MagicMock(returncode=0)
        with mock.patch.object(self.runner, "load_config",
                               return_value={
                                   "stages": [{"name": "dev", "tracks": ["dummy-track"]}],
                                   "environments": {
                                       "dev-local": {
                                           "prepare_env": {
                                               "script": "/bin/true",
                                               "args": [],
                                               "timeout_seconds": 30,
                                           }
                                       }
                                   }
                               }), \
             mock.patch.object(self.runner, "_resolve_stage_env",
                               return_value="dev-local"), \
             mock.patch.object(self.runner, "_phase_log_path",
                               return_value=log_path), \
             mock.patch.object(self.common, "_subprocess") as mock_sp, \
             mock.patch.dict(sys.modules, {"pg_context_chain": mock.MagicMock()}):
            mock_sp.run.return_value = fake_proc_success
            mock_chain = sys.modules["pg_context_chain"]
            mock_chain.phase_start = mock.MagicMock()
            mock_chain.phase_end = mock.MagicMock()
            result = self.common.execute_env_hook_inline(self.change, "prepare_env")
        self.assertTrue(result["success"])
        self.assertFalse(result["skipped"])
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["env_name"], "dev-local")
        self.assertEqual(result["phase_item"], "dev.prepare_env")
        # Context-chain phase_start and phase_end both called.
        mock_chain.phase_start.assert_called_once_with(self.change, "dev.prepare_env")
        mock_chain.phase_end.assert_called_once()
        end_args = mock_chain.phase_end.call_args[0]
        self.assertEqual(end_args[0], self.change)
        self.assertEqual(end_args[1], "dev.prepare_env")
        # Log file should exist (subprocess.run was called with stdout=log file).
        self.assertTrue(os.path.isfile(log_path))

    def test_failure_path_returns_result_with_nonzero_exit(self):
        # Hook script `false` exits 1. Result should reflect failure.
        # Mock _subprocess.run to short-circuit (pg-run-hook not in test env).
        log_path = os.path.join(self.tmpdir, "hook-fail.log")
        fake_proc_fail = mock.MagicMock(returncode=1)
        with mock.patch.object(self.runner, "load_config",
                               return_value={
                                   "stages": [{"name": "dev", "tracks": ["dummy-track"]}],
                                   "environments": {
                                       "dev-local": {
                                           "prepare_env": {
                                               "script": "/bin/false",
                                               "args": [],
                                               "timeout_seconds": 30,
                                           }
                                       }
                                   }
                               }), \
             mock.patch.object(self.runner, "_resolve_stage_env",
                               return_value="dev-local"), \
             mock.patch.object(self.runner, "_phase_log_path",
                               return_value=log_path), \
             mock.patch.object(self.common, "_subprocess") as mock_sp, \
             mock.patch.dict(sys.modules, {"pg_context_chain": mock.MagicMock()}):
            mock_sp.run.return_value = fake_proc_fail
            mock_chain = sys.modules["pg_context_chain"]
            mock_chain.phase_start = mock.MagicMock()
            mock_chain.phase_end = mock.MagicMock()
            result = self.common.execute_env_hook_inline(self.change, "prepare_env")
        self.assertFalse(result["success"])
        self.assertFalse(result["skipped"])
        self.assertEqual(result["exit_code"], 1)
        # phase_end should still be called (with failure summary).
        mock_chain.phase_end.assert_called_once()

    def test_bootstrap_raises_envhook_on_failure(self):
        # pg_build_bootstrap must raise EnvHookError when execute_env_hook_inline
        # reports failure. This is the regression guard for the build-r Step 3
        # bug where phase_result handling was broken.
        from pg_pipeline_common import EnvHookError
        class FakePS:
            def __init__(self):
                self.data = {"context": {}}
                self.committed = 0
            def commit(self):
                self.committed += 1
        ps = FakePS()
        with mock.patch.object(self.runner, "_ensure_context_chain"), \
             mock.patch.object(self.runner, "migrate_legacy_state_files", return_value=[]), \
             mock.patch.object(self.runner, "_maybe_bootstrap_init_commit",
                               return_value={"committed": True}), \
             mock.patch.object(self.common, "execute_env_hook_inline",
                               return_value={"success": False, "skipped": False,
                                             "log_path": "/tmp/x.log",
                                             "exit_code": 1,
                                             "env_name": "dev-local",
                                             "phase_item": "dev.prepare_env",
                                             "error": "exit_code=1"}):
            with self.assertRaises(EnvHookError) as ctx:
                self.common.pg_build_bootstrap(self.change, ps)
        self.assertEqual(ctx.exception.phase_name, "prepare_env")
        self.assertEqual(ctx.exception.exit_code, 1)

    def test_bootstrap_skips_envhook_on_v1_state(self):
        # v1 state is a plain dict (no .data attribute). pg_build_bootstrap
        # must NOT execute env-hook for v1 (v1 keeps the phase_result path).
        class V1Dict(dict):
            pass
        state = V1Dict(init_committed=False)
        with mock.patch.object(self.runner, "_ensure_context_chain"), \
             mock.patch.object(self.runner, "migrate_legacy_state_files", return_value=[]), \
             mock.patch.object(self.runner, "_maybe_bootstrap_init_commit",
                               return_value={"committed": True}), \
             mock.patch.object(self.common, "execute_env_hook_inline") as mock_exec:
            self.common.pg_build_bootstrap(self.change, state)
        mock_exec.assert_not_called()

    def test_bootstrap_records_environment_summary_on_success(self):
        # On env-hook success, state.context.environment_summary must be
        # populated so cmd_next_v2 can attach it to the first dispatch.
        class FakePS:
            def __init__(self):
                self.data = {"context": {}}
                self.committed = 0
            def commit(self):
                self.committed += 1
        ps = FakePS()
        with mock.patch.object(self.runner, "_ensure_context_chain"), \
             mock.patch.object(self.runner, "migrate_legacy_state_files", return_value=[]), \
             mock.patch.object(self.runner, "_maybe_bootstrap_init_commit",
                               return_value={"committed": True}), \
             mock.patch.object(self.runner, "load_config",
                               return_value={
                                   "environments": {
                                       "dev-local": {
                                           "roles": {
                                               "backend": {"instances": [
                                                   {"name": "backend-1",
                                                    "host": "localhost",
                                                    "port": 9080}]}
                                           }
                                       }
                                   }
                               }), \
             mock.patch.object(self.common, "execute_env_hook_inline",
                               return_value={"success": True, "skipped": False,
                                             "log_path": "/tmp/x.log",
                                             "exit_code": 0,
                                             "env_name": "dev-local",
                                             "phase_item": "dev.prepare_env"}):
            self.common.pg_build_bootstrap(self.change, ps)
        summary = ps.data["context"].get("environment_summary")
        self.assertIsNotNone(summary)
        self.assertEqual(summary["name"], "dev-local")
        self.assertEqual(summary["prepare_env_log_path"], "/tmp/x.log")
        self.assertIn("backend", summary["instances"])
        self.assertEqual(summary["instances"]["backend"][0]["port"], 9080)


if __name__ == "__main__":
    unittest.main()
