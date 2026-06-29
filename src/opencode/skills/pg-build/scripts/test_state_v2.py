#!/usr/bin/env python3
"""test_state_v2.py — Unit tests for PipelineState (v2 schema).

Tests follow the plan §3 Step 1 acceptance criteria:
  - test_next_pending_walks_TDVG_in_order
  - test_record_completed_test_advances_to_dev
  - test_record_completed_dev_advances_to_verify
  - test_verify_escalate_creates_fix_cycle
  - test_verify_completed_after_fix_advances_to_gate
  - test_gate_pass_marks_track_completed
  - test_gate_fail_creates_gate_fix_cycle
  - test_gate_fix_exhausted_marks_track_completed_with_accepted_gaps
  - test_render_tasks_checkboxes_reflects_state
  - test_commit_atomic_rename
  + extra tests covering v1→v2 migration, idempotent resume, etc.

Run:
  cd /home/ubuntu/workspace/pg-skills/src/opencode/skills/pg-build/scripts
  python3 -m unittest -v test_state_v2.py

Or with pytest:
  python3 -m pytest test_state_v2.py -v
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

# Make the scripts dir importable when run from anywhere
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

from pg_pipeline_state_v2 import (
    PipelineState,
    NextDispatch,
    PHASE_AGENTS,
    SCHEMA_VERSION,
)


def _make_project(layout: dict) -> str:
    """Create a temp project with .pg/project.yaml + given changes/tasks.

    layout keys:
      "changes": {change_name: {"state": dict_or_None, "tasks_md": str_or_None}}
    """
    tmp = tempfile.mkdtemp(prefix="pg_v2_test_")
    pg_dir = os.path.join(tmp, ".pg")
    os.makedirs(pg_dir)
    with open(os.path.join(pg_dir, "project.yaml"), "w") as f:
        f.write("# minimal project.yaml for v2 tests\n")
    changes_dir = os.path.join(tmp, ".pg", "changes")
    for change, spec in layout.get("changes", {}).items():
        cdir = os.path.join(changes_dir, change)
        build_dir = os.path.join(cdir, "2-build")
        os.makedirs(build_dir, exist_ok=True)
        if spec.get("state") is not None:
            with open(os.path.join(build_dir, ".pipeline-state.json"), "w") as f:
                json.dump(spec["state"], f)
        if spec.get("tasks_md"):
            with open(os.path.join(cdir, "tasks.md"), "w") as f:
                f.write(spec["tasks_md"])
    return tmp


def _empty_v1(change: str, order: list = None) -> dict:
    return {
        "version": 1,
        "change": change,
        "failed": False,
        "current": None,
        "completed_items": [],
        "pipeline_order": order or ["dev.backend"],
    }


# ────────────────────────────────────────────────────────────────────
# Test cases
# ────────────────────────────────────────────────────────────────────

class TestPipelineStateBasics(unittest.TestCase):
    """Group 1: construction, persistence, atomic write."""

    def setUp(self):
        self.tmp = _make_project({})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_init_creates_empty_state_when_no_file(self):
        ps = PipelineState("demo", project_root=self.tmp)
        self.assertEqual(ps.data["version"], 2)
        self.assertEqual(ps.data["schema_version"], SCHEMA_VERSION)
        self.assertEqual(ps.data["change"], "demo")
        self.assertEqual(ps.data["tracks"], {})
        self.assertIsNone(ps.data["current_dispatch"])
        self.assertEqual(ps.data["dispatch_history"], [])
        self.assertFalse(ps.data["context"]["completed"])
        self.assertFalse(ps.data["context"]["failed"])

    def test_commit_atomic_rename(self):
        ps = PipelineState("demo", project_root=self.tmp)
        ps.set_pipeline_order(["dev.backend", "dev.frontend"])
        ps.init_track("dev.backend", label="后端")
        ps.record_dispatch_started("dev.backend", "test", "pg-build/test")
        ps.commit()

        # No leftover .tmp files
        leftovers = [
            f for f in os.listdir(os.path.join(self.tmp, ".pg/changes/demo/2-build"))
            if f.startswith(".pipeline-state.") and f.endswith(".tmp")
        ]
        self.assertEqual(leftovers, [], f"atomic write left .tmp files: {leftovers}")

        # Reload returns same content
        ps2 = PipelineState("demo", project_root=self.tmp)
        self.assertEqual(ps2.data["context"]["pipeline_order"], ["dev.backend", "dev.frontend"])
        self.assertIn("dev.backend", ps2.data["tracks"])

    def test_init_track_idempotent(self):
        ps = PipelineState("demo", project_root=self.tmp)
        ps.init_track("dev.backend", label="first")
        first_started_at = ps.data["tracks"]["dev.backend"]["started_at"]
        ps.init_track("dev.backend", label="second")  # no-op
        self.assertEqual(ps.data["tracks"]["dev.backend"]["label"], "first")
        self.assertEqual(ps.data["tracks"]["dev.backend"]["started_at"], first_started_at)


class TestNextPending(unittest.TestCase):
    """Group 2: next_pending() walks TDVG in order."""

    def setUp(self):
        self.tmp = _make_project({})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_next_pending_walks_TDVG_in_order(self):
        ps = PipelineState("c1", project_root=self.tmp)
        ps.set_pipeline_order(["dev.backend"])
        nd = ps.next_pending()
        self.assertIsNotNone(nd)
        self.assertEqual(nd.track, "dev.backend")
        self.assertEqual(nd.phase, "test")
        self.assertEqual(nd.agent, "pg-build/test")
        self.assertEqual(nd.kind, "dispatch")

    def test_record_completed_test_advances_to_dev(self):
        ps = PipelineState("c2", project_root=self.tmp)
        ps.set_pipeline_order(["dev.backend"])
        ps.record_dispatch_started("dev.backend", "test", "pg-build/test")
        ps.record_completed("dev.backend", "test", summary="tests written")

        nd = ps.next_pending()
        self.assertEqual(nd.phase, "dev")
        self.assertEqual(nd.track, "dev.backend")

    def test_record_completed_dev_advances_to_verify(self):
        ps = PipelineState("c3", project_root=self.tmp)
        ps.set_pipeline_order(["dev.backend"])
        ps.record_dispatch_started("dev.backend", "test", "pg-build/test")
        ps.record_completed("dev.backend", "test")
        ps.record_dispatch_started("dev.backend", "dev", "pg-build/dev")
        ps.record_completed("dev.backend", "dev")

        nd = ps.next_pending()
        self.assertEqual(nd.phase, "verify")
        self.assertEqual(nd.agent, "pg-build/verify")

    def test_record_completed_verify_advances_to_gate(self):
        ps = PipelineState("c4", project_root=self.tmp)
        ps.set_pipeline_order(["dev.backend"])
        ps.record_dispatch_started("dev.backend", "test", "pg-build/test")
        ps.record_completed("dev.backend", "test")
        ps.record_dispatch_started("dev.backend", "dev", "pg-build/dev")
        ps.record_completed("dev.backend", "dev")
        ps.record_dispatch_started("dev.backend", "verify", "pg-build/verify")
        ps.record_completed("dev.backend", "verify", report_path="verify-report.md")

        nd = ps.next_pending()
        self.assertEqual(nd.phase, "gate")
        self.assertEqual(nd.agent, "pg-build/gate")

    def test_next_pending_skips_completed_tracks(self):
        ps = PipelineState("c5", project_root=self.tmp)
        ps.set_pipeline_order(["dev.backend", "dev.frontend"])
        ps.record_dispatch_started("dev.backend", "test", "pg-build/test")
        ps.record_completed("dev.backend", "test")
        ps.record_dispatch_started("dev.backend", "dev", "pg-build/dev")
        ps.record_completed("dev.backend", "dev")
        ps.record_dispatch_started("dev.backend", "verify", "pg-build/verify")
        ps.record_completed("dev.backend", "verify")
        ps.record_dispatch_started("dev.backend", "gate", "pg-build/gate")
        ps.record_pass("dev.backend", summary="gate passed")

        nd = ps.next_pending()
        self.assertEqual(nd.track, "dev.frontend")
        self.assertEqual(nd.phase, "test")

    def test_next_pending_returns_final_gate_when_all_done(self):
        ps = PipelineState("c6", project_root=self.tmp)
        ps.set_pipeline_order(["dev.backend"])
        for phase in ("test", "dev", "verify"):
            ps.record_dispatch_started("dev.backend", phase, PHASE_AGENTS[phase])
            ps.record_completed("dev.backend", phase)
        ps.record_dispatch_started("dev.backend", "gate", "pg-build/gate")
        ps.record_pass("dev.backend")

        nd = ps.next_pending()
        self.assertEqual(nd.kind, "dispatch_final_gate")
        self.assertEqual(nd.track, "final-gate")


class TestFixCycles(unittest.TestCase):
    """Group 3: verify-fix and gate-fix loops."""

    def setUp(self):
        self.tmp = _make_project({})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _setup_through_verify(self) -> PipelineState:
        ps = PipelineState("fix1", project_root=self.tmp)
        ps.set_pipeline_order(["dev.backend"])
        for phase in ("test", "dev"):
            ps.record_dispatch_started("dev.backend", phase, PHASE_AGENTS[phase])
            ps.record_completed("dev.backend", phase)
        ps.record_dispatch_started("dev.backend", "verify", "pg-build/verify")
        return ps

    def test_verify_escalate_creates_fix_cycle(self):
        ps = self._setup_through_verify()
        ps.record_escalate("dev.backend", summary="test regression")

        # Verify cycles appended
        verify = ps.data["tracks"]["dev.backend"]["phases"]["verify"]
        self.assertEqual(len(verify["cycles"]), 1)
        self.assertEqual(verify["cycles"][0]["status"], "escalate")
        # fix_cycles also appended
        self.assertEqual(len(verify["fix_cycles"]), 1)
        self.assertEqual(verify["fix_cycles"][0]["sub"], "fix")

        nd = ps.next_pending()
        self.assertEqual(nd.kind, "dispatch_fix")
        self.assertEqual(nd.phase, "fix")
        self.assertEqual(nd.agent, "pg-build/fix")

    def test_verify_completed_after_fix_advances_to_gate(self):
        ps = self._setup_through_verify()
        ps.record_escalate("dev.backend")
        # fix agent dispatch + completed
        ps.record_dispatch_started("dev.backend", "fix", "pg-build/fix")
        ps.record_fix_completed("dev.backend", "verify", summary="fix1 applied",
                                 fixed_tasks=[2])

        nd = ps.next_pending()
        # Now verify should be re-dispatched (verify cycle 2)
        self.assertEqual(nd.phase, "verify")
        self.assertEqual(nd.kind, "dispatch")

    def test_gate_fail_creates_gate_fix_cycle(self):
        ps = self._setup_through_verify()
        ps.record_completed("dev.backend", "verify")
        ps.record_dispatch_started("dev.backend", "gate", "pg-build/gate")
        ps.record_fail("dev.backend", summary="G-1: security gap", fixed_tasks=[4])

        nd = ps.next_pending()
        self.assertEqual(nd.kind, "dispatch_fix")
        self.assertEqual(nd.phase, "fix-gate")
        self.assertEqual(nd.agent, "pg-build/fix-gate")

    def test_gate_fix_exhausted_marks_track_completed_with_accepted_gaps(self):
        ps = self._setup_through_verify()
        ps.record_completed("dev.backend", "verify")
        ps.record_dispatch_started("dev.backend", "gate", "pg-build/gate")
        ps.record_fail("dev.backend", summary="G-1", fixed_tasks=[4])
        # Exhaust after max_gate_fix_retries (default 2)
        ps.record_dispatch_started("dev.backend", "fix-gate", "pg-build/fix-gate")
        ps.record_fix_completed("dev.backend", "gate", summary="fix-gate 1 done")
        # gate fails again
        ps.record_fail("dev.backend", summary="G-1 persists", fixed_tasks=[4])
        ps.record_dispatch_started("dev.backend", "fix-gate", "pg-build/fix-gate")
        ps.record_fix_completed("dev.backend", "gate", summary="fix-gate 2 done")

        # Decision 2: exhausted → track completed with accepted_gaps
        ps.record_gate_exhausted(
            "dev.backend",
            accepted_gaps=[{"gap_id": "G-1", "description": "known gap"}],
            report_path="gate-report.md",
        )

        track = ps.data["tracks"]["dev.backend"]
        self.assertEqual(track["status"], "completed")
        self.assertIn("accepted_gaps", track)
        self.assertEqual(track["accepted_gaps"][0]["gap_id"], "G-1")

        # gate.phase.accepted_gaps recorded too
        gate = track["phases"]["gate"]
        self.assertEqual(gate.get("accepted_gaps")[0]["gap_id"], "G-1")


class TestIdempotentResume(unittest.TestCase):
    """Group 4: current_dispatch supports idempotent resume."""

    def setUp(self):
        self.tmp = _make_project({})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_idempotent_resume_returns_same_dispatch(self):
        ps = PipelineState("resume1", project_root=self.tmp)
        ps.set_pipeline_order(["dev.backend"])
        ps.record_dispatch_started("dev.backend", "test", "pg-build/test")

        nd1 = ps.next_pending()
        nd2 = ps.next_pending()
        self.assertEqual(nd1.track, nd2.track)
        self.assertEqual(nd1.phase, nd2.phase)
        self.assertTrue(nd1.is_resume)
        self.assertTrue(nd2.is_resume)

    def test_record_completed_clears_resume_after_advance(self):
        ps = PipelineState("resume2", project_root=self.tmp)
        ps.set_pipeline_order(["dev.backend"])
        ps.record_dispatch_started("dev.backend", "test", "pg-build/test")
        ps.record_completed("dev.backend", "test")

        nd = ps.next_pending()
        self.assertFalse(nd.is_resume)
        self.assertEqual(nd.phase, "dev")


class TestGatePass(unittest.TestCase):
    """Group 5: gate pass marks track completed."""

    def setUp(self):
        self.tmp = _make_project({})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_gate_pass_marks_track_completed(self):
        ps = PipelineState("gp1", project_root=self.tmp)
        ps.set_pipeline_order(["dev.backend"])
        for phase in ("test", "dev", "verify"):
            ps.record_dispatch_started("dev.backend", phase, PHASE_AGENTS[phase])
            ps.record_completed("dev.backend", phase)
        ps.record_dispatch_started("dev.backend", "gate", "pg-build/gate")
        ps.record_pass("dev.backend", summary="all good", report_path="gate.md")

        self.assertEqual(ps.data["tracks"]["dev.backend"]["status"], "completed")
        gate = ps.data["tracks"]["dev.backend"]["phases"]["gate"]
        self.assertEqual(gate["status"], "pass")
        self.assertEqual(len(gate["gate_cycles"]), 1)
        self.assertEqual(gate["gate_cycles"][0]["status"], "pass")

    def test_final_gate_pass_marks_workflow_completed(self):
        ps = PipelineState("gp2", project_root=self.tmp)
        ps.set_pipeline_order(["dev.backend"])
        # Skip ahead to final-gate via direct mutation
        ps._data["context"]["completed"] = False  # explicit
        ps.record_dispatch_started("final-gate", "gate", "pg-build/gate")
        ps.record_pass("final-gate")

        self.assertTrue(ps.data["context"]["completed"])
        self.assertIsNone(ps.data["current_dispatch"])


class TestV1ToV2Migration(unittest.TestCase):
    """Group 6: from_v1_state() translates v1 → v2."""

    def setUp(self):
        self.tmp = _make_project({})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_translates_v1_with_current(self):
        v1 = _empty_v1("v1c")
        v1["current"] = {
            "item": "dev.backend",
            "sub": "fix",
            "attempt": 2,
            "fix_cycles": 1,
            "waiting": True,
            "in_fix_cycle": True,
        }
        ps = PipelineState.from_v1_state(v1, "v1c", project_root=self.tmp)

        self.assertEqual(ps.data["version"], 2)
        cd = ps.data["current_dispatch"]
        self.assertEqual(cd["track"], "dev.backend")
        self.assertEqual(cd["phase"], "fix")
        self.assertEqual(cd["attempt"], 2)
        self.assertEqual(cd["cycle"], 2)
        self.assertTrue(cd["waiting"])

    def test_translates_v1_with_completed_items(self):
        v1 = _empty_v1("v1d")
        v1["completed_items"] = ["dev.backend"]
        ps = PipelineState.from_v1_state(v1, "v1d", project_root=self.tmp)
        self.assertEqual(ps.data["tracks"]["dev.backend"]["status"], "completed")

    def test_translates_v1_with_workflow_completed(self):
        v1 = _empty_v1("v1e")
        v1["completed"] = True
        ps = PipelineState.from_v1_state(v1, "v1e", project_root=self.tmp)
        self.assertTrue(ps.data["context"]["completed"])


class TestRenderTasksCheckboxes(unittest.TestCase):
    """Group 7: render_tasks_checkboxes reflects state."""

    def setUp(self):
        self.tmp = _make_project({})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_render_tasks_checkboxes_reflects_state(self):
        ps = PipelineState("r1", project_root=self.tmp)
        ps.set_pipeline_order(["dev.backend"])
        ps.record_dispatch_started("dev.backend", "test", "pg-build/test")
        ps.record_completed("dev.backend", "test", tasks_marked=[1, 2])
        ps.record_dispatch_started("dev.backend", "dev", "pg-build/dev")
        ps.record_completed("dev.backend", "dev", tasks_marked=[3])

        out = ps.render_tasks_checkboxes()
        self.assertIn("dev.backend", out)
        self.assertIn("test", out)
        self.assertIn("dev", out)
        # Phase test marked 1,2
        self.assertIn("任务 1", out)
        self.assertIn("任务 2", out)
        self.assertIn("任务 3", out)


class TestTaskMarkedCLI(unittest.TestCase):
    """Group 8: record_task_marked appends to phase.tasks_marked."""

    def setUp(self):
        self.tmp = _make_project({})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_record_task_marked_appends(self):
        ps = PipelineState("tm1", project_root=self.tmp)
        ps.set_pipeline_order(["dev.backend"])
        ps.record_dispatch_started("dev.backend", "test", "pg-build/test")
        ps.record_task_marked("dev.backend", "test", 1)
        ps.record_task_marked("dev.backend", "test", 2)
        ps.record_task_marked("dev.backend", "test", 1)  # idempotent
        marked = ps.data["tracks"]["dev.backend"]["phases"]["test"]["tasks_marked"]
        self.assertEqual(sorted(marked), [1, 2])


class TestWorkflowFailed(unittest.TestCase):
    """Group 9: mark_workflow_failed terminates flow."""

    def setUp(self):
        self.tmp = _make_project({})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_workflow_failed_terminal(self):
        ps = PipelineState("wf1", project_root=self.tmp)
        ps.set_pipeline_order(["dev.backend"])
        ps.mark_workflow_failed("gate failed after 5 attempts")

        self.assertTrue(ps.data["context"]["failed"])
        self.assertEqual(ps.data["context"]["failed_reason"],
                         "gate failed after 5 attempts")
        self.assertIsNone(ps.data["current_dispatch"])
        nd = ps.next_pending()
        self.assertIsNone(nd)


class TestDispatchHistory(unittest.TestCase):
    """Group 10: dispatch_history is append-only."""

    def setUp(self):
        self.tmp = _make_project({})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dispatch_seq_increments(self):
        ps = PipelineState("dh1", project_root=self.tmp)
        ps.set_pipeline_order(["dev.backend"])
        e1 = ps.record_dispatch_started("dev.backend", "test", "pg-build/test")
        e2 = ps.record_dispatch_started("dev.backend", "dev", "pg-build/dev")
        self.assertEqual(e1["seq"], "001")
        self.assertEqual(e2["seq"], "002")
        self.assertEqual(len(ps.data["dispatch_history"]), 2)

    def test_dispatch_history_persists_result_kind(self):
        ps = PipelineState("dh2", project_root=self.tmp)
        ps.set_pipeline_order(["dev.backend"])
        ps.record_dispatch_started("dev.backend", "test", "pg-build/test")
        ps.record_completed("dev.backend", "test", summary="done")
        last = ps.data["dispatch_history"][-1]
        self.assertEqual(last["result"], "completed")
        self.assertIn("result_at", last)


if __name__ == "__main__":
    unittest.main(verbosity=2)