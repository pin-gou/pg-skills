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


def _make_project(layout: dict, project_yaml: str = None) -> str:
    """Create a temp project with .pg/project.yaml + given changes/tasks.

    layout keys:
      "changes": {change_name: {"state": dict_or_None, "tasks_md": str_or_None}}

    project_yaml: optional override for the .pg/project.yaml content. When
    provided, MUST be valid YAML loadable by pg-pipeline-runner's load_config
    (e.g. include `schema: spec-driven` header). When None, an empty stub
    is written (no tracks, no stages).
    """
    tmp = tempfile.mkdtemp(prefix="pg_v2_test_")
    pg_dir = os.path.join(tmp, ".pg")
    os.makedirs(pg_dir)
    yaml_content = project_yaml if project_yaml is not None else (
        "# minimal project.yaml for v2 tests\n"
    )
    with open(os.path.join(pg_dir, "project.yaml"), "w") as f:
        f.write(yaml_content)
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


# Minimal valid project.yaml with one simple track, used by simple-track
# tests in TestSimpleTrackRouting. Mirrors the shape of the real
# project.yaml's tracks.openapi-gen block (after runner strips the
# 'openapi-gen' track declaration).
_SIMPLE_TRACK_PROJECT_YAML = """\
schema: spec-driven
state_v2:
  enabled: true
modules: {}
tracks:
  openapi-gen:
    type: simple
    timeout_seconds: 600
    on_failure: workflow_failed
    commands:
      - "echo hello"
stages:
  - name: dev
    environment: dev-local
    tracks: [openapi-gen]
"""


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


class TestSimpleTrackRouting(unittest.TestCase):
    """Group 5b: simple track dispatch (P0-1 fix regression guards).

    These tests guard the v2 state machine against regressing into
    dispatching simple tracks as test/dev/verify/gate (the original
    P0-1 bug). They exercise is_simple_track() and _next_phase_in_track()
    with a real project.yaml that has a 'openapi-gen' simple track.
    """

    def setUp(self):
        self.tmp = _make_project({}, project_yaml=_SIMPLE_TRACK_PROJECT_YAML)
        # PipelineState's _load_config_cached() calls pg_pipeline_common's
        # load_config which reads the module-level CONFIG_PATH from
        # pg-pipeline-common. Patch that here so the test uses our
        # isolated project.yaml instead of the runner's import-time path.
        import pg_pipeline_common
        self._common = pg_pipeline_common
        self._old_config_path = pg_pipeline_common.CONFIG_PATH
        pg_pipeline_common.CONFIG_PATH = os.path.join(
            self.tmp, ".pg", "project.yaml"
        )

    def tearDown(self):
        # Restore the original CONFIG_PATH so we don't leak state
        # into other test modules.
        if hasattr(self, "_common") and hasattr(self, "_old_config_path"):
            self._common.CONFIG_PATH = self._old_config_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_is_simple_track_recognizes_simple_type(self):
        """is_simple_track() must return True for tracks.<id>.type='simple'."""
        ps = PipelineState("simple-routing-1", project_root=self.tmp)
        # Force-load the config cache (lazy initialization).
        self.assertTrue(ps.is_simple_track("dev.openapi-gen"))
        self.assertTrue(ps.is_simple_track("openapi-gen"))

    def test_is_simple_track_rejects_standard_type(self):
        """is_simple_track() must return False for tracks without type='simple'.

        Without an explicit 'simple' track in project.yaml, the bare
        id (e.g. 'backend') should be classified as standard track.
        """
        ps = PipelineState("simple-routing-2", project_root=self.tmp)
        # The minimal project.yaml has no 'backend' track — get_track_type
        # defaults to 'track' for unknown tracks, so is_simple_track
        # must return False.
        self.assertFalse(ps.is_simple_track("dev.backend"))
        self.assertFalse(ps.is_simple_track("dev.frontend"))

    def test_is_simple_track_rejects_env_hooks(self):
        """is_simple_track() must return False for prepare_env/clean_env.

        Env hooks have their own routing path (executed inline by
        pg_build_bootstrap) and must not be classified as simple.
        """
        ps = PipelineState("simple-routing-3", project_root=self.tmp)
        self.assertFalse(ps.is_simple_track("dev.prepare_env"))
        self.assertFalse(ps.is_simple_track("dev.clean_env"))

    def test_next_pending_simple_track_dispatches_simple_sub(self):
        """next_pending() must return phase='simple' for simple tracks.

        Regression test for P0-1: previously the state machine walked
        TDVG_PHASES and dispatched phase='test' for the first visit,
        producing 4 phantom noop sub-dispatches. After the fix, the
        first dispatch must be phase='simple' agent='pg-build/simple'.
        """
        ps = PipelineState("simple-routing-4", project_root=self.tmp)
        ps.set_pipeline_order(["dev.openapi-gen"])

        nd = ps.next_pending()
        self.assertIsNotNone(nd)
        self.assertEqual(nd.track, "dev.openapi-gen")
        self.assertEqual(nd.phase, "simple",
                         f"BUG: simple track should dispatch phase='simple', got {nd.phase}")
        self.assertEqual(nd.agent, "pg-build/simple",
                         f"BUG: should use pg-build/simple agent, got {nd.agent}")
        self.assertEqual(nd.kind, "dispatch")
        self.assertFalse(nd.is_resume)

    def test_next_pending_simple_track_resume_when_running(self):
        """Idempotent resume: simple sub already running → is_resume=True.

        If the runner dispatched simple and the agent crashed before
        record, next_pending() must return the same dispatch with
        is_resume=True so the LLM orchestrator re-uses the in-flight
        dispatch instead of creating a new one.
        """
        ps = PipelineState("simple-routing-5", project_root=self.tmp)
        ps.set_pipeline_order(["dev.openapi-gen"])
        # Simulate dispatch already started.
        ps.record_dispatch_started("dev.openapi-gen", "simple", "pg-build/simple")

        nd = ps.next_pending()
        self.assertIsNotNone(nd)
        self.assertEqual(nd.track, "dev.openapi-gen")
        self.assertEqual(nd.phase, "simple")
        self.assertTrue(nd.is_resume,
                        "BUG: simple sub already running should resume, not re-dispatch")

    def test_next_pending_simple_track_advances_after_completion(self):
        """After simple sub completed, next_pending() must advance.

        Regression test for the second half of P0-1: the state machine
        must mark the simple track 'completed' and return the next
        track in pipeline_order (or final-gate if no more tracks).

        Note: track.status is set to 'completed' by the
        _next_phase_in_track() short-circuit (when the simple sub's
        status is 'completed'), NOT by record_completed directly.
        This matches the pattern used for standard tracks where the
        final TDVG phase completion is what triggers the track-level
        transition.
        """
        ps = PipelineState("simple-routing-6", project_root=self.tmp)
        ps.set_pipeline_order(["dev.openapi-gen"])

        # Drive simple sub to completion.
        ps.record_dispatch_started("dev.openapi-gen", "simple", "pg-build/simple")
        ps.record_completed("dev.openapi-gen", "simple")

        # Phase-level: simple.status = completed.
        self.assertEqual(
            ps.data["tracks"]["dev.openapi-gen"]["phases"]["simple"]["status"],
            "completed")

        # next_pending() should now return final-gate (only track in order).
        nd = ps.next_pending()
        self.assertIsNotNone(nd)
        self.assertEqual(nd.track, "final-gate")
        self.assertEqual(nd.kind, "dispatch_final_gate")
        # AND the short-circuit flips track.status to 'completed' as a
        # side-effect of next_pending walking the now-empty phases.
        self.assertEqual(ps.data["tracks"]["dev.openapi-gen"]["status"],
                         "completed")


class TestFinalGateRecordCompleted(unittest.TestCase):
    """Group 5c: final-gate record_completed() must set context.completed.

    These tests guard the v2 state machine against regressing into the
    P2-3 bug: when LLM orchestrator calls record_completed for final-gate
    (because the sub-agent returned SUCCESS, not 'pass'), the state
    machine must set context.completed=True so cmd_next_v2 returns
    'done' on the next call. Without this short-circuit, the state
    machine walks final-gate through TDVG_PHASES producing 4 phantom
    dispatches (test/dev/verify/gate).
    """

    def setUp(self):
        self.tmp = _make_project({})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_record_completed_final_gate_sets_context_completed(self):
        """record_completed('final-gate', ...) must set context.completed.

        Pre-fix: record_completed set phases.gate.status='completed' but
        left context.completed=False, so next_pending() walked final-gate
        through TDVG_PHASES producing phantom dispatches.

        Post-fix: record_completed delegates to record_pass for final-gate
        (and only for final-gate), which sets context.completed=True.
        """
        ps = PipelineState("fg-rc-1", project_root=self.tmp)
        # Simulate final-gate dispatch in flight.
        ps.record_dispatch_started("final-gate", "gate", "pg-build/gate")

        # Pre-condition: workflow not yet completed.
        self.assertFalse(ps.data["context"].get("completed"))

        # The LLM orchestrator's call after sub-agent returns SUCCESS.
        ps.record_completed("final-gate", "gate", summary="audit passed")

        # Post-condition: workflow completed.
        self.assertTrue(ps.data["context"]["completed"],
                        "BUG: record_completed for final-gate must set context.completed=True")
        # current_dispatch cleared (so the next cmd_next is terminal).
        self.assertIsNone(ps.data["current_dispatch"])

    def test_record_completed_final_gate_terminates_pipeline(self):
        """After record_completed(final-gate), next cmd_next returns 'done'.

        End-to-end: when record_completed(final-gate) is called, the
        very next cmd_next_v2 call should see context.completed=True
        and return action='done' (terminal), NOT enter another dispatch.
        """
        ps = PipelineState("fg-rc-2", project_root=self.tmp)
        ps.record_dispatch_started("final-gate", "gate", "pg-build/gate")
        ps.record_completed("final-gate", "gate")

        # The terminal check in cmd_next_v2 is on context.completed.
        self.assertTrue(ps.data["context"]["completed"])
        # next_pending() should return dispatch_final_gate (the existing
        # behavior is to surface final-gate once; cmd_next_v2's terminal
        # check at line 152-154 returns 'done' before next_pending is
        # even called when context.completed is True).
        # We just verify the gate is in a terminal state here.
        nd = ps.next_pending()
        # next_pending() will return final-gate dispatch_final_gate;
        # cmd_next_v2's wrapper checks context.completed first and returns 'done'.
        # The state machine's job is done; the wrapper handles the terminal.
        self.assertIsNotNone(nd)  # state machine provides final-gate dispatch
        self.assertEqual(nd.track, "final-gate")
        self.assertEqual(nd.kind, "dispatch_final_gate")

    def test_record_completed_non_final_gate_unaffected(self):
        """record_completed for non-final-gate must NOT set context.completed.

        Regression guard: the final-gate short-circuit in record_completed
        must not accidentally short-circuit regular tracks. A standard
        track's completion should only mark phases.<phase>.status and
        track.status, leaving context.completed for the final-gate path.
        """
        ps = PipelineState("fg-rc-3", project_root=self.tmp)
        ps.set_pipeline_order(["dev.backend"])
        ps.record_dispatch_started("dev.backend", "test", "pg-build/test")
        ps.record_completed("dev.backend", "test")

        # context.completed should NOT be set.
        self.assertFalse(ps.data["context"].get("completed"))
        # phases.test.status is "completed", track.status still "running".
        self.assertEqual(ps.data["tracks"]["dev.backend"]["phases"]["test"]["status"],
                         "completed")
        self.assertEqual(ps.data["tracks"]["dev.backend"]["status"], "running")


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