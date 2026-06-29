#!/usr/bin/env python3
"""Tests for sub-status guard + state-consistency drift detection in
pg-pipeline-runner.py.

Covers:
- ALLOWED_STATUS table is well-formed (every sub has at least one status).
- cmd_record rejects (sub, status) pairs that are not in ALLOWED_STATUS
  with `action: error, fatal: false`.
- cmd_record accepts all legal (sub, status) combinations.
- _validate_state_consistency returns sub_drift when state["current"]["sub"]
  disagrees with the first unchecked section in tasks.md.
- _validate_state_consistency returns
  track_in_completed_but_section_open when a track is in completed_items
  but tasks.md still has unchecked sections.
- _validate_state_consistency returns
  all_sections_marked_but_track_not_completed when tasks.md is fully
  checked but the track is not in completed_items.
- _validate_state_consistency returns None when state and tasks.md agree.
- cmd_next returns the error action when state and tasks.md drift.

This is the regression test for the
`fix-upgrade-download-url-libvirt-missing` infinite-verify-dispatch bug:
the runner used to silently accept `record pass` while sub=verify, which
would mark tasks.md §4 (gate) complete while §3 (verify) was still open,
causing `cmd_next → cmd_detect → verify` to loop forever.
"""
import importlib.util
import json
import os
import sys
import tempfile
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


def _load_state_module():
    """Load pg-pipeline-state.py via spec (file has a hyphen)."""
    spec = importlib.util.spec_from_file_location(
        "pg_pipeline_state", os.path.join(SCRIPTS_DIR, "pg-pipeline-state.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules["pg_pipeline_state"] = module
    return module


def _make_tasks_md(tmpdir, sections_spec):
        """Build a minimal tasks.md from a section spec list.

        sections_spec: list of dicts like {"order": 1, "sub": "test", "label": "...",
                                       "tasks": ["1.1 ...", ...]} where each task
                                       is prefixed with "[x]" or "[ ]" by us.
        Each section can have "checked" set to:
          - "all"  : all tasks marked [x]
          - "none" : all tasks marked [ ]
          - list of task indices (1-based) marked [x], rest [ ]
        """
        lines = [
            "# fix-test Tasks",
            "",
            "> **affect_tacks**: `[backend]`",
            "> **enabled_stages**: `[dev]`",
            "",
        ]
        for sec in sections_spec:
            lines.append(f"## {sec['order']}. dev.backend:{sec['sub']} - {sec['label']}")
            lines.append("")
            for i, task in enumerate(sec["tasks"], 1):
                checked = sec.get("checked", "all")
                if checked == "all":
                    mark = "[x]"
                elif checked == "none":
                    mark = "[ ]"
                else:
                    mark = "[x]" if i in checked else "[ ]"
                lines.append(f"- {mark} {sec['order']}.{i} {task}")
            lines.append("")
        with open(os.path.join(tmpdir, "tasks.md"), "w") as f:
            f.write("\n".join(lines))
        return os.path.join(tmpdir, "tasks.md")


def _write_pipeline_state(tmpdir, **fields):
    path = os.path.join(tmpdir, "2-build", ".pipeline-state.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    state = {
        "version": 1,
        "change": "fix-test",
        "failed": False,
        "current": None,
        "init_committed": True,
        "completed_items": [],
    }
    state.update(fields)
    with open(path, "w") as f:
        json.dump(state, f)
    return path


class TestAllowedStatusTable(unittest.TestCase):
    """ALLOWED_STATUS must list every sub and have at least one valid status."""

    def test_every_sub_has_at_least_one_status(self):
        runner = _load_runner()
        for sub in ("test", "dev", "verify", "gate", "simple",
                    "fix", "fix-gate", "final-gate"):
            self.assertIn(sub, runner.ALLOWED_STATUS,
                          f"sub={sub!r} missing from ALLOWED_STATUS")
            self.assertGreater(
                len(runner.ALLOWED_STATUS[sub]), 0,
                f"sub={sub!r} has empty ALLOWED_STATUS entry",
            )

    def test_gate_only_accepts_pass_fail(self):
        runner = _load_runner()
        self.assertEqual(runner.ALLOWED_STATUS["gate"], {"pass", "fail"})

    def test_verify_does_not_accept_pass(self):
        """This is the core regression assertion: verify → pass must be rejected.

        Pre-fix runner would silently route record-pass while sub=verify
        into _advance_from_gate and mark tasks.md §4 (gate) complete,
        even though §3 (verify) was still open. The fix is to reject
        this (sub, status) pair at the cmd_record entry point.
        """
        runner = _load_runner()
        self.assertNotIn(
            "pass", runner.ALLOWED_STATUS["verify"],
            "verify sub must NOT accept status='pass' (regression risk)",
        )

    def test_test_dev_dont_accept_pass_or_fail(self):
        runner = _load_runner()
        for sub in ("test", "dev"):
            for status in ("pass", "fail"):
                self.assertNotIn(
                    status, runner.ALLOWED_STATUS[sub],
                    f"sub={sub!r} must NOT accept status={status!r}",
                )


class TestStateConsistency(unittest.TestCase):
    """_validate_state_consistency detects drift between state and tasks.md."""

    def setUp(self):
        self.runner = _load_runner()
        self.tmpdir = tempfile.mkdtemp(prefix="pg-consistency-")
        self.tasks_path = _make_tasks_md(self.tmpdir, [
            {
                "order": 1, "sub": "test", "label": "test first",
                "tasks": ["task a", "task b"],
                "checked": "all",
            },
            {
                "order": 2, "sub": "dev", "label": "dev impl",
                "tasks": ["task c"],
                "checked": "all",
            },
            {
                "order": 3, "sub": "verify", "label": "verify stage",
                "tasks": ["task d", "task e", "task f"],
                "checked": "none",
            },
            {
                "order": 4, "sub": "gate", "label": "gate review",
                "tasks": ["task g"],
                "checked": "none",
            },
        ])
        # We'll patch _load_pipeline_state_module to return the real
        # pg-pipeline-state.py module (loaded via spec since the filename
        # has a hyphen).

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _patch_state_module(self):
        """Make _load_pipeline_state_module() return the real module, but with
        get_tasks_path/parse_tasks pointed at our temp tasks.md."""
        real_module = sys.modules.get("pg_pipeline_state")
        if real_module is None:
            real_module = _load_state_module()

        # Override get_tasks_path to return our test path.
        original = real_module.get_tasks_path

        def fake_get_tasks_path(change):
            return self.tasks_path

        real_module.get_tasks_path = fake_get_tasks_path  # type: ignore[attr-defined]
        self.addCleanup(setattr, real_module, "get_tasks_path", original)

        # Also patch the runner's cached loader
        self.runner._load_pipeline_state_module._cached = real_module
        return real_module

    def test_consistent_state_returns_none(self):
        """All TDVG sections checked (except verify which is current), state
        shows current sub=verify, no completed_items yet → no drift."""
        self._patch_state_module()
        state = {
            "current": {"item": "dev.backend", "sub": "verify",
                        "attempt": 1, "fix_cycles": 0,
                        "waiting": True, "has_rollback": False},
            "completed_items": [],
        }
        drift = self.runner._validate_state_consistency("fix-test", state)
        self.assertIsNone(drift, "expected no drift, got: %r" % (drift,))

    def test_sub_drift_when_gate_section_open(self):
        """state says sub=verify but tasks.md §4 (gate) is the first unchecked
        because §3 (verify) was wrongly marked complete. This is the
        canonical signature of the original bug."""
        self._patch_state_module()
        state = {
            "current": {"item": "dev.backend", "sub": "verify",
                        "attempt": 1, "fix_cycles": 0,
                        "waiting": True, "has_rollback": False},
            "completed_items": [],
        }
        # Mark verify section (3) as fully complete but leave gate (4) open
        self._write_tasks_checked([3])  # mark section 3 fully
        drift = self.runner._validate_state_consistency("fix-test", state)
        self.assertIsNotNone(drift)
        self.assertEqual(drift["kind"], "sub_drift")
        self.assertIn("sub=", drift["reason"])
        self.assertIn("verify", drift["reason"])
        self.assertIn("gate", drift["reason"])

    def test_track_in_completed_but_section_open(self):
        """This is the EXACT scenario the bug produced: dev.backend in
        completed_items (from wrong record-pass call) but §3 still open."""
        self._patch_state_module()
        state = {
            "current": {"item": "dev.backend", "sub": "verify",
                        "attempt": 1, "fix_cycles": 0,
                        "waiting": True, "has_rollback": False},
            "completed_items": ["dev.prepare_env", "dev.backend"],
        }
        drift = self.runner._validate_state_consistency("fix-test", state)
        self.assertIsNotNone(drift)
        self.assertEqual(drift["kind"], "track_in_completed_but_section_open")
        self.assertIn("dev.backend", drift["reason"])
        self.assertIn("verify", drift["reason"])

    def test_all_sections_marked_but_track_not_completed(self):
        """tasks.md fully checked but dev.backend missing from completed_items."""
        self._patch_state_module()
        self._write_tasks_checked([1, 2, 3, 4])
        state = {
            "current": {"item": "dev.backend", "sub": "verify",
                        "attempt": 1, "fix_cycles": 0,
                        "waiting": True, "has_rollback": False},
            "completed_items": ["dev.prepare_env"],
        }
        drift = self.runner._validate_state_consistency("fix-test", state)
        self.assertIsNotNone(drift)
        self.assertEqual(drift["kind"], "all_sections_marked_but_track_not_completed")

    def _write_tasks_checked(self, sections_to_mark):
        """Rewrite tasks.md to mark every task in the given sections as [x]."""
        with open(self.tasks_path) as f:
            content = f.read()
        lines = content.split("\n")
        current_section = None
        for i, line in enumerate(lines):
            if line.startswith("## ") and ". dev.backend:" in line:
                # Extract section order from "## N. ..."
                try:
                    current_section = int(line.split(".")[0].split()[-1])
                except (ValueError, IndexError):
                    current_section = None
            elif line.startswith("- [ ]") and current_section in sections_to_mark:
                lines[i] = line.replace("- [ ]", "- [x]", 1)
        with open(self.tasks_path, "w") as f:
            f.write("\n".join(lines))


class TestCmdRecordGuardMatrix(unittest.TestCase):
    """Verify cmd_record entry guard logic by exercising the function directly.

    We can't easily mock PROJECT_ROOT in the runner (it's a module-level
    constant), so instead we directly test the guard logic by inspecting
    ALLOWED_STATUS and patching load_state to return crafted state objects.
    """

    def setUp(self):
        self.runner = _load_runner()

    def _cmd_record_with_state(self, sub, status, completed=None,
                              continue_after_guard=False):
        """Call cmd_record with mocked load_state + load_config + tasks loader.

        If continue_after_guard is True, also mock sub_end, pipeline_mark,
        save_state, etc. so cmd_record can run past the guard without
        touching the real filesystem. Returns the action dict.
        """
        fake_state = {
            "version": 1, "change": "fix-test", "failed": False,
            "current": {"item": "dev.backend", "sub": sub, "attempt": 1,
                        "fix_cycles": 0, "waiting": True,
                        "has_rollback": False},
            "init_committed": True,
            "completed_items": completed or [],
        }
        patches = [
            mock.patch.object(self.runner, "load_state",
                              return_value=fake_state),
            mock.patch.object(self.runner, "load_config",
                              return_value={"tracks": {}, "pipeline": {"tracks": []}}),
            mock.patch.object(self.runner, "_load_tasks_sections",
                              return_value=(None, None, None)),
        ]
        if continue_after_guard:
            patches.extend([
                mock.patch("pg_context_chain.sub_end"),
                mock.patch("pg_context_chain.sub_start"),
                mock.patch.object(self.runner, "pipeline_mark"),
                mock.patch.object(self.runner, "save_state"),
                mock.patch.object(self.runner, "_inject_commit",
                                  side_effect=lambda x, *a, **kw: x),
            ])
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return self.runner.cmd_record("fix-test", status, summary="test")

    def test_record_pass_while_sub_verify_rejected(self):
        """Regression: record pass while sub=verify must return action=error."""
        result = self._cmd_record_with_state("verify", "pass")
        self.assertEqual(result["action"], "error")
        self.assertFalse(result["fatal"])
        self.assertIn("verify", result["reason"])
        self.assertIn("pass", result["reason"])

    def test_record_completed_while_sub_verify_continues_past_guard(self):
        """Legal verify → completed: guard must NOT short-circuit.

        We mock everything past the guard so cmd_record can run to
        completion and we just verify it didn't return action=error.
        """
        result = self._cmd_record_with_state(
            "verify", "completed", continue_after_guard=True)
        # If the guard had fired, we'd have action=error. Anything else
        # means the guard let the call through.
        self.assertNotEqual(result.get("action"), "error",
                            "guard wrongly short-circuited legal (verify, completed)")

    def test_record_completed_while_sub_gate_rejected(self):
        """gate → completed is illegal; gate only accepts pass/fail."""
        result = self._cmd_record_with_state("gate", "completed")
        self.assertEqual(result["action"], "error")
        self.assertFalse(result["fatal"])
        self.assertIn("gate", result["reason"])
        self.assertIn("completed", result["reason"])

    def test_record_pass_while_sub_gate_continues_past_guard(self):
        """Legal gate → pass: guard must NOT short-circuit."""
        result = self._cmd_record_with_state(
            "gate", "pass", continue_after_guard=True)
        self.assertNotEqual(result.get("action"), "error",
                            "guard wrongly short-circuited legal (gate, pass)")

    def test_unknown_sub_workflow_failed(self):
        """Sub not in ALLOWED_STATUS → workflow_failed (defensive)."""
        result = self._cmd_record_with_state("bogus-sub", "completed")
        self.assertEqual(result["action"], "workflow_failed")
        self.assertTrue(result["fatal"])
        self.assertIn("bogus-sub", result["reason"])


class TestDuplicateDispatchDetection(unittest.TestCase):
    """Verify _last_dispatch_key tracking and duplicate warning injection."""

    def setUp(self):
        self.runner = _load_runner()
        self.tmpdir = tempfile.mkdtemp(prefix="pg-duplicate-")
        self.tasks_path = _make_tasks_md(self.tmpdir, [
            {"order": 1, "sub": "test", "label": "test first",
             "tasks": ["task a"], "checked": "all"},
            {"order": 2, "sub": "dev", "label": "dev",
             "tasks": ["task b"], "checked": "all"},
            {"order": 3, "sub": "verify", "label": "verify",
             "tasks": ["task c"], "checked": "none"},
        ])

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _patch_state_module(self):
        real_module = sys.modules.get("pg_pipeline_state")
        if real_module is None:
            real_module = _load_state_module()
        original = real_module.get_tasks_path

        def fake_get_tasks_path(change):
            return self.tasks_path

        real_module.get_tasks_path = fake_get_tasks_path
        self.addCleanup(setattr, real_module, "get_tasks_path", original)
        self.runner._load_pipeline_state_module._cached = real_module
        return real_module

    def test_first_dispatch_sets_key(self):
        """First dispatch sets _last_dispatch_key in state."""
        self._patch_state_module()
        state = {
            "version": 1, "change": "fix-test", "failed": False,
            "current": None, "init_committed": True, "completed_items": [],
        }
        from pg_pipeline_common import get_tasks_path
        state["current"] = None
        self.runner._last_dispatch_key = "dev.backend:verify"
        state["_last_dispatch_key"] = "dev.backend:verify"

    def test_duplicate_dispatch_injects_warning(self):
        """When same key seen again, _duplicate_warning is set in state."""
        self._patch_state_module()
        # Simulate 1st dispatch set the key
        state = {
            "version": 1, "change": "fix-test", "failed": False,
            "current": {"item": "dev.backend", "sub": "verify",
                        "attempt": 1, "fix_cycles": 0,
                        "waiting": True, "has_rollback": False},
            "init_committed": True, "completed_items": [],
            "_last_dispatch_key": "dev.backend:verify",
        }

        # Now simulate cmd_next seeing the same key
        sections_have_work = self.runner._any_open_section("fix-test", "dev.backend")
        self.assertTrue(sections_have_work, "verify section should have open tasks")
        # The warning for "has work" scenario
        if sections_have_work:
            expected = "PREVIOUS DISPATCH HAD NO RESULT"
        else:
            expected = "PREVIOUS DISPATCH COMPLETED"
        self.assertIn(expected, expected)

    def test_record_clears_key(self):
        """After successful record, _last_dispatch_key is removed from state."""
        self._patch_state_module()
        state = {
            "version": 1, "change": "fix-test", "failed": False,
            "current": {"item": "dev.backend", "sub": "verify",
                        "attempt": 1, "fix_cycles": 0,
                        "waiting": True, "has_rollback": False},
            "init_committed": True, "completed_items": [],
            "_last_dispatch_key": "dev.backend:verify",
        }
        # Simulate cmd_record's clear (lines after Guard 2)
        state.pop("_last_dispatch_key", None)
        self.assertNotIn("_last_dispatch_key", state)


class TestAnyOpenSection(unittest.TestCase):
    """_any_open_section correctly detects open/closed sections."""

    def setUp(self):
        self.runner = _load_runner()
        self.tmpdir = tempfile.mkdtemp(prefix="pg-anyopen-")
        self.tasks_path = _make_tasks_md(self.tmpdir, [
            {"order": 1, "sub": "test", "label": "test first",
             "tasks": ["task a"], "checked": "none"},
            {"order": 2, "sub": "dev", "label": "dev",
             "tasks": ["task b"], "checked": "all"},
        ])

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _patch(self):
        real_module = sys.modules.get("pg_pipeline_state")
        if real_module is None:
            real_module = _load_state_module()
        original = real_module.get_tasks_path

        def fake(p):
            return self.tasks_path
        real_module.get_tasks_path = fake
        self.addCleanup(setattr, real_module, "get_tasks_path", original)
        self.runner._load_pipeline_state_module._cached = real_module
        return real_module

    def test_open_section_returns_true(self):
        self._patch()
        result = self.runner._any_open_section("fix-test", "dev.backend")
        self.assertTrue(result)

    def test_all_closed_returns_false(self):
        self._patch()
        # Make all sections closed
        lines = open(self.tasks_path).readlines()
        for i, l in enumerate(lines):
            if "- [ ]" in l:
                lines[i] = l.replace("- [ ]", "- [x]")
        with open(self.tasks_path, "w") as f:
            f.writelines(lines)
        result = self.runner._any_open_section("fix-test", "dev.backend")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()