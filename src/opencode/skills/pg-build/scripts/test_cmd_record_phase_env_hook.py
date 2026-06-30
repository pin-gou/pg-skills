#!/usr/bin/env python3
"""Tests for env-hook phase (prepare_env / clean_env) record handling.

Validates that `cmd_record` correctly handles `phase_result` → `record
completed/failed` for env-hook phases, which are the fix for the
`instance-detail-host-versions` bug where `record completed` after
`prepare_env` returned `workflow_failed: No active item to record`.

Covers (v1 cmd_record only; v2 cmd_record_v2 is tested by the shadow
test below):
  1. prepare_env completed → dispatches next item via cmd_next
  2. prepare_env failed → workflow_failed
  3. clean_env failed → advance (non-blocking)
  4. None current → unchanged workflow_failed
  5. prepare_env record with illegal status → error
"""
import importlib.util
import os
import unittest
from unittest import mock


SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
RUNNER_PY = os.path.join(SCRIPTS_DIR, "pg-pipeline-runner.py")


def _load_runner():
    spec = importlib.util.spec_from_file_location("pg_pipeline_runner", RUNNER_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestEnvHookRecordGuard(unittest.TestCase):
    """Guard 0 / _is_env_hook_phase detection + _handle_env_hook_record."""

    def setUp(self):
        self.runner = _load_runner()

    def _fake_state(self, item="dev.prepare_env", sub=None,
                    waiting=False, completed_items=None):
        return {
            "version": 1,
            "change": "fix-test",
            "current": {"item": item, "sub": sub, "waiting": waiting},
            "completed_items": completed_items or [],
            "init_committed": True,
            "tracks": {},
            "context": {"pipeline_order": [], "completed": False, "failed": False},
        }

    def test_prepare_env_completed_advances(self):
        """prepare_env completed → _handle_env_hook_record → cmd_next
        (dispatch next item, not workflow_failed)."""
        state = self._fake_state()
        with (
            mock.patch.object(self.runner, "load_state", return_value=state),
            mock.patch.object(self.runner, "load_config",
                              return_value={"tracks": {}, "pipeline": {"tracks": []},
                                            "stages": [], "environments": {}}),
            mock.patch.object(self.runner, "_load_tasks_sections",
                              return_value=(None, None, None)),
            mock.patch.object(self.runner, "cmd_next", return_value={
                "action": "dispatch", "item": "dev.backend", "sub": "test"}),
            mock.patch.object(self.runner, "save_state"),
            mock.patch.object(self.runner, "_inject_commit",
                              side_effect=lambda x, *a, **kw: x),
            mock.patch("pg_context_chain.phase_end"),
        ):
            result = self.runner.cmd_record("fix-test", "completed",
                                            summary="env ready")
            self.assertEqual(result["action"], "dispatch",
                             "prepare_env completed should advance to next dispatch")
            self.assertEqual(result["item"], "dev.backend")
            self.assertIsNone(state["current"],
                              "current must be released after env-hook record")

    def test_prepare_env_failed_workflow_failed(self):
        """prepare_env failed → workflow_failed (terminal)."""
        state = self._fake_state()
        with (
            mock.patch.object(self.runner, "load_state", return_value=state),
            mock.patch.object(self.runner, "load_config",
                              return_value={"tracks": {}, "pipeline": {"tracks": []},
                                            "stages": [], "environments": {}}),
            mock.patch.object(self.runner, "_load_tasks_sections",
                              return_value=(None, None, None)),
            mock.patch.object(self.runner, "save_state"),
            mock.patch.object(self.runner, "_inject_commit",
                              side_effect=lambda x, *a, **kw: x),
            mock.patch("pg_context_chain.phase_end"),
        ):
            result = self.runner.cmd_record(
                "fix-test", "failed", summary="setup timeout")
            self.assertEqual(result["action"], "workflow_failed")
            self.assertTrue(result["fatal"])
            self.assertIn("setup timeout", result["reason"])

    def test_clean_env_failed_advances(self):
        """clean_env failed is non-blocking; advances like cmd_next()."""
        state = self._fake_state(item="dev.clean_env")
        with (
            mock.patch.object(self.runner, "load_state", return_value=state),
            mock.patch.object(self.runner, "load_config",
                              return_value={"tracks": {}, "pipeline": {"tracks": []},
                                            "stages": [], "environments": {}}),
            mock.patch.object(self.runner, "_load_tasks_sections",
                              return_value=(None, None, None)),
            mock.patch.object(self.runner, "cmd_next", return_value={
                "action": "dispatch", "item": "dev.backend", "sub": "test"}),
            mock.patch.object(self.runner, "save_state"),
            mock.patch.object(self.runner, "_inject_commit",
                              side_effect=lambda x, *a, **kw: x),
            mock.patch("pg_context_chain.phase_end"),
        ):
            result = self.runner.cmd_record(
                "fix-test", "failed", summary="cleanup timeout")
            self.assertEqual(result["action"], "dispatch",
                             "clean_env failed must advance, not abort")
            self.assertEqual(result["item"], "dev.backend")

    def test_no_current_returns_workflow_failed(self):
        """No current item → unchanged workflow_failed."""
        state = self._fake_state()
        state["current"] = None
        with (
            mock.patch.object(self.runner, "load_state", return_value=state),
            mock.patch.object(self.runner, "load_config",
                              return_value={"tracks": {}, "pipeline": {"tracks": []},
                                            "stages": [], "environments": {}}),
            mock.patch.object(self.runner, "_inject_commit",
                              side_effect=lambda x, *a, **kw: x),
        ):
            result = self.runner.cmd_record("fix-test", "completed")
            self.assertEqual(result.get("action"), "workflow_failed")
            self.assertIn("No active item to record", result.get("reason", ""))

    def test_prepare_env_illegal_status_error(self):
        """prepare_env with 'pass' status → error (only completed|failed)."""
        state = self._fake_state()
        with (
            mock.patch.object(self.runner, "load_state", return_value=state),
            mock.patch.object(self.runner, "load_config",
                              return_value={"tracks": {}, "pipeline": {"tracks": []},
                                            "stages": [], "environments": {}}),
            mock.patch.object(self.runner, "_inject_commit",
                              side_effect=lambda x, *a, **kw: x),
        ):
            result = self.runner.cmd_record("fix-test", "pass")
            self.assertEqual(result.get("action"), "error")
            self.assertFalse(result.get("fatal", True))
            self.assertIn("env-hook", result.get("reason", ""))

    def test_normal_sub_guard_unaffected(self):
        """Non-env-hook sub (test/completed) still passes guard normally."""
        state = self._fake_state(item="dev.backend", sub="test")
        with (
            mock.patch.object(self.runner, "load_state", return_value=state),
            mock.patch.object(self.runner, "load_config",
                              return_value={"tracks": {}, "pipeline": {"tracks": []},
                                            "stages": [], "environments": {}}),
            mock.patch.object(self.runner, "_load_tasks_sections",
                              return_value=(None, None, None)),
            mock.patch.object(self.runner, "pipeline_mark"),
            mock.patch.object(self.runner, "save_state"),
            mock.patch.object(self.runner, "_advance_to_next_sub",
                              return_value={"action": "dispatch", "item": "dev.backend",
                                            "sub": "dev"}),
            mock.patch.object(self.runner, "_inject_commit",
                              side_effect=lambda x, *a, **kw: x),
            mock.patch("pg_context_chain.sub_end"),
        ):
            result = self.runner.cmd_record("fix-test", "completed")
            self.assertNotEqual(result.get("action"), "error",
                                "normal sub agent record must not be blocked by Guard 0")


if __name__ == "__main__":
    unittest.main()
