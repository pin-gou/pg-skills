"""Test _collect_missing_gate_assessments (v2.7: trust snapshot.report_path)."""

import os
import tempfile
import unittest

from pipeline.events import FINAL_GATE_TRACK
from pipeline.orchestrator import Orchestrator, PhaseState, TrackState, PipelineState, save_snapshot


def _make_file(directory: str, filename: str, content: str = "# PASS") -> str:
    path = os.path.join(directory, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _setup_orch(
    tmp_root: str,
    gate_report_path: str | None = None,
    gate_report_exists: bool = True,
    use_gate_verify_suffix: bool = False,
) -> Orchestrator:
    """Setup orchestrator with one completed track (dev.backend) for gate precheck."""
    build_dir = os.path.join(tmp_root, "2-build")
    os.makedirs(build_dir, exist_ok=True)

    # Write the gate report file (if it should exist)
    if gate_report_path and gate_report_exists:
        os.makedirs(os.path.dirname(gate_report_path), exist_ok=True)
        _make_file(os.path.dirname(gate_report_path), os.path.basename(gate_report_path))

    # Also write a file in 2-build/ for glob fallback if needed
    if use_gate_verify_suffix and not gate_report_path:
        _make_file(build_dir, "006-dev.backend-gate-verify.md")

    state = PipelineState(
        change="test-change",
        pipeline_order=("dev.backend", "dev.frontend", FINAL_GATE_TRACK),
        status="running",
        tracks={
            "dev.backend": TrackState.create(
                "dev.backend",
                status="completed",
                modules=("backend",),
                phases={
                    "gate": PhaseState(
                        status="pass",
                        report_path=gate_report_path,
                        summary="gate_score: 90, p0_failures: []",
                    ),
                },
            ),
            "dev.frontend": TrackState.create(
                "dev.frontend",
                status="completed",
                modules=("frontend",),
                phases={
                    "gate": PhaseState(
                        status="pass",
                        report_path=None,
                        summary="gate_score: 90, p0_failures: []",
                    ),
                },
            ),
        },
    )
    save_snapshot(tmp_root, state)
    orch = Orchestrator("test-change")
    orch.change_root = tmp_root
    orch.state = state
    return orch


class TestGatePrecheckV2_7(unittest.TestCase):
    """v2.7: _collect_missing_gate_assessments 优先信任 snapshot.report_path."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        # Cleanup temp files
        pass

    def test_trust_report_path_hit(self):
        """Case 1: snapshot.report_path 指向真实文件 → pass（即使 glob 不匹配 -gate.md）"""
        report_path = os.path.join(self.tmp, "2-build", "006-dev.backend-gate-verify.md")
        orch = _setup_orch(self.tmp, gate_report_path=report_path, gate_report_exists=True)
        missing = orch._collect_missing_gate_assessments()
        self.assertNotIn("dev.backend", missing, "trust report_path should make dev.backend pass")
        self.assertIn("dev.frontend", missing, "dev.frontend has no report_path and no glob match")

    def test_trust_report_path_miss(self):
        """Case 2: snapshot.report_path 指向不存在文件 + glob 无 -gate.md → missing"""
        report_path = os.path.join(self.tmp, "2-build", "nonexistent-gate.md")
        orch = _setup_orch(self.tmp, gate_report_path=report_path, gate_report_exists=False)
        missing = orch._collect_missing_gate_assessments()
        self.assertIn("dev.backend", missing, "report_path missing + no glob match → missing")
        self.assertIn("dev.frontend", missing, "dev.frontend also missing")

    def test_glob_fallback_standard_gate_md(self):
        """Case 3: glob fallback with -gate.md naming → pass"""
        build_dir = os.path.join(self.tmp, "2-build")
        os.makedirs(build_dir, exist_ok=True)
        _make_file(build_dir, "006-dev.backend-gate.md")

        # dev.backend has no report_path - will fallback to glob
        orch = _setup_orch(self.tmp, gate_report_path=None)
        missing = orch._collect_missing_gate_assessments()
        self.assertNotIn("dev.backend", missing, "glob -gate.md should match")
        self.assertIn("dev.frontend", missing, "dev.frontend has no -gate.md file")

    def test_glob_fallback_gate_verify_not_matched(self):
        """Case 4: glob fallback only matches -gate.md, NOT -gate-verify.md"""
        build_dir = os.path.join(self.tmp, "2-build")
        os.makedirs(build_dir, exist_ok=True)
        _make_file(build_dir, "006-dev.backend-gate-verify.md")

        orch = _setup_orch(self.tmp, gate_report_path=None)
        missing = orch._collect_missing_gate_assessments()
        self.assertIn("dev.backend", missing, "glob only matches -gate.md, NOT -gate-verify.md")

    def test_simple_track_skipped(self):
        """Simple track should be skipped."""
        build_dir = os.path.join(self.tmp, "2-build")
        os.makedirs(build_dir, exist_ok=True)

        state = PipelineState(
            change="test-change",
            pipeline_order=("dev.openapi-gen", "dev.backend", FINAL_GATE_TRACK),
            track_types={"dev.openapi-gen": "simple"},
            status="running",
            tracks={
                "dev.openapi-gen": TrackState.create("dev.openapi-gen", status="completed"),
                "dev.backend": TrackState.create(
                    "dev.backend",
                    status="completed",
                    modules=("backend",),
                    phases={
                        "gate": PhaseState(
                            status="pass",
                            report_path=os.path.join(self.tmp, "2-build", "006-dev.backend-gate.md"),
                        ),
                    },
                ),
            },
        )
        save_snapshot(self.tmp, state)
        orch = Orchestrator("test-change")
        orch.change_root = self.tmp
        orch.state = state

        _make_file(build_dir, "006-dev.backend-gate.md")
        missing = orch._collect_missing_gate_assessments()
        self.assertNotIn("dev.openapi-gen", missing, "simple track should be skipped")
        self.assertNotIn("dev.backend", missing, "dev.backend has -gate.md file")


if __name__ == "__main__":
    unittest.main()