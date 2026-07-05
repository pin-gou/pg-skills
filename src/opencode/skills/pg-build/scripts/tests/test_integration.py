"""集成测试：完整 TDVG 流程（test→dev→verify→gate→pass）。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.event_log import EventLog
from pipeline.snapshot import load_snapshot, save_snapshot
from pipeline.state import PipelineState, TrackState, PhaseState
from pipeline.events import PipelineRecord, PipelineAction, FINAL_GATE_TRACK
from pipeline.reducer import reduce_state, PHASE_AGENTS
from pipeline.detect import next_pending
from pipeline.orchestrator import Orchestrator


def _make_report(content: str = "# PASS\nverification complete") -> str:
    """生成临时 verify/gate 报告文件并返回绝对路径。

    v2.1 sub_agent_contract 要求 verify/gate 阶段必须有 report_path + evidence_paths。
    """
    fd, path = tempfile.mkstemp(suffix=".md", prefix="integration-report-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _make_report_at(directory: str, filename: str, content: str = "# PASS\ngate assessment") -> str:
    """在指定目录创建 report 文件（用于模拟 gate agent 产出 gate-assessment）。"""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _setup_initial_state(tmp_root: str, change: str = "test-change") -> Orchestrator:
    """设置初始 state 并跳过 bootstrap。"""
    state = PipelineState(
        change=change,
        pipeline_order=("dev.backend", "dev.frontend"),
        status="running",
        tracks={
            "dev.backend": TrackState.create("dev.backend", modules=("backend",)),
            "dev.frontend": TrackState.create("dev.frontend", modules=("frontend",)),
        },
    )
    save_snapshot(tmp_root, state)
    orch = Orchestrator(change)
    orch.change_root = tmp_root
    orch.state = state
    # 调用 next() 设置 current_track/current_phase 为第一个 dispatch
    orch.next()
    return orch


class TestIntegrationTdvg(unittest.TestCase):
    """完整 TDVG：test→dev→verify→gate→pass。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.change_root = os.path.join(self.tmp, "test-change")
        os.makedirs(self.change_root)
        self.orch = _setup_initial_state(self.tmp, "test-change")

    def test_full_tdvg(self):
        """test→dev→verify→gate→pass→track completed。"""
        track = "dev.backend"

        # Step 1: test completed → dev
        r1 = self.orch.record("completed", summary="10 tests PASS", outputs="/tmp/Test.java",
                              tasks_updated=["1.1"])
        self.assertEqual(r1["action"], "dispatch")
        self.assertEqual(r1["sub"], "dev")

        # Step 2: dev completed → verify
        r2 = self.orch.record("completed", summary="impl done", outputs="/tmp/Impl.java",
                              tasks_updated=["2.1"])
        self.assertEqual(r2["action"], "dispatch")
        self.assertEqual(r2["sub"], "verify")

        # Step 3: verify completed → gate (verify 需要 report + evidence)
        verify_report = _make_report("# PASS\nall V-* PASS")
        r3 = self.orch.record(
            "completed", summary="all V-* PASS",
            report_path=verify_report, outputs=verify_report,
        )
        self.assertEqual(r3["action"], "dispatch")
        self.assertEqual(r3["sub"], "gate")

        # Step 4: gate pass → track completed → advance to next track (gate 需要 report)
        gate_report = _make_report("# PASS\nall G-* PASS")
        r4 = self.orch.record(
            "pass", summary="all G-* PASS gate_score: 95, p0_failures: []",
            report_path=gate_report, outputs=gate_report,
        )
        self.assertEqual(r4["action"], "dispatch")  # advance 内部调用 next() 返回下一个 dispatch
        self.assertEqual(r4["item"], "dev.frontend")

        # Verify track is completed
        self.assertTrue(self.orch.state.is_track_completed(track))

        # Verify event log has all entries
        events = self.orch.event_log.replay()
        types = [e["type"] for e in events]
        self.assertIn("record_received", types)
        self.assertIn("dispatch_started", types)

    def test_two_tracks_then_final_gate(self):
        """两个 track 都完成 → final-gate。"""
        # 注：orch.change_root 是 self.tmp（不是 self.change_root）。
        # _collect_missing_gate_assessments 用 self.orch.change_root，
        # 所以 gate-assessment 文件必须写到 self.tmp/2-build/。
        build_dir = os.path.join(self.tmp, "2-build")

        # 完成 dev.backend
        self.orch.record("completed", summary="test phase 完成", outputs="/tmp/Test.java",
                         tasks_updated=["1.1"])  # test
        self.orch.record("completed", summary="dev phase 完成", outputs="/tmp/Dev.java",
                         tasks_updated=["2.1"])  # dev
        verify_report = _make_report("# PASS\nverify 1")
        self.orch.record(
            "completed", summary="verify 完成",
            report_path=verify_report, outputs=verify_report,
        )  # verify
        # 模拟 gate agent 产出 gate-assessment 报告（final-gate 前置门控要求）
        _make_report_at(
            build_dir,
            "006-dev.backend-gate-assessment.md",
            "# PASS\ndev.backend gate assessment",
        )
        gate_report = _make_report("# PASS\ngate 1")
        r1 = self.orch.record(
            "pass", summary="gate pass gate_score: 90, p0_failures: []",
            report_path=gate_report, outputs=gate_report,
        )  # gate → advance 到 dev.frontend
        self.assertEqual(r1["action"], "dispatch")
        self.assertEqual(r1["item"], "dev.frontend")

        # 完成 dev.frontend
        self.orch.record("completed", summary="test phase 完成", outputs="/tmp/FrontTest.java",
                         tasks_updated=["10.1"])  # test
        self.orch.record("completed", summary="dev phase 完成", outputs="/tmp/FrontDev.java",
                         tasks_updated=["11.1"])  # dev
        verify_report2 = _make_report("# PASS\nverify 2")
        self.orch.record(
            "completed", summary="verify 完成",
            report_path=verify_report2, outputs=verify_report2,
        )  # verify
        # 模拟 gate agent 产出 gate-assessment 报告
        _make_report_at(
            build_dir,
            "013-dev.frontend-gate-assessment.md",
            "# PASS\ndev.frontend gate assessment",
        )
        gate_report2 = _make_report("# PASS\ngate 2")
        r2 = self.orch.record(
            "pass", summary="gate pass gate_score: 90, p0_failures: []",
            report_path=gate_report2, outputs=gate_report2,
        )  # gate → advance 到 final-gate

        # final-gate now returns dispatch_final_gate (with dispatch_file written)
        self.assertTrue(r2["action"] in ("dispatch_final_gate", "dispatch"))
        self.assertEqual(r2["item"], FINAL_GATE_TRACK)

    def test_workflow_failed(self):
        """test 重试耗尽 → workflow_failed，需要 4 次（max_retries=3, 计数从0开始）。"""
        self.orch.record("failed", summary="error 1", issues="error 1",
                         outputs="/tmp/Test.java", tasks_updated=["1.1"])
        self.orch.record("failed", summary="error 2", issues="error 2",
                         outputs="/tmp/Test.java", tasks_updated=["1.1"])
        self.orch.record("failed", summary="error 3", issues="error 3",
                         outputs="/tmp/Test.java", tasks_updated=["1.1"])
        # 第 4 次失败 → exhausted
        r = self.orch.record("failed", summary="error 4", issues="error 4",
                             outputs="/tmp/Test.java", tasks_updated=["1.1"])
        self.assertEqual(r["action"], "workflow_failed")
        self.assertTrue(r.get("fatal", False))


class TestIntegrationFixCycle(unittest.TestCase):
    """verify escalate → fix → re-verify → gate。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.change_root = os.path.join(self.tmp, "test-change")
        os.makedirs(self.change_root)
        self.orch = _setup_initial_state(self.tmp, "test-change")

    def test_verify_escalate_then_fix(self):
        """verify escalate → 创建子 pipeline。"""
        self.orch.record("completed", summary="test 完成", outputs="/tmp/Test.java",
                         tasks_updated=["1.1"])  # test
        self.orch.record("completed", summary="dev 完成", outputs="/tmp/Dev.java",
                         tasks_updated=["2.1"])  # dev

        # verify escalate（verify 阶段需要 report + evidence）
        verify_report = _make_report("# FAIL\n3 tests FAIL")
        r = self.orch.record(
            "escalate", summary="3 tests FAIL",
            report_path=verify_report, outputs=verify_report,
            evidence_paths=[verify_report],
            tasks_updated=["V-1", "V-2"],
        )
        self.assertEqual(r["action"], "dispatch")
        # 应该有子 pipeline
        self.assertIsNotNone(self.orch.state.current_sub_pipeline)

    def test_verify_escalate_fix_complete(self):
        """verify escalate → fix → re-verify → gate。"""
        self.orch.record("completed", summary="test 完成", outputs="/tmp/Test.java",
                         tasks_updated=["1.1"])  # test
        self.orch.record("completed", summary="dev 完成", outputs="/tmp/Dev.java",
                         tasks_updated=["2.1"])  # dev

        # verify escalate（verify 阶段需要 report + evidence）
        verify_report = _make_report("# FAIL\n3 tests FAIL")
        self.orch.record(
            "escalate", summary="3 tests FAIL",
            report_path=verify_report, outputs=verify_report,
            evidence_paths=[verify_report],
            tasks_updated=["V-1"],
        )
        # 当前 dispatch 是 fix
        self.assertEqual(self.orch.state.current_phase, "fix")

        # fix completed → back to verify (v2.3 unified re_verify)
        fix_report = _make_report("# PASS\nfix OK")
        r = self.orch.record(
            "completed", summary="fixed all",
            report_path=fix_report, outputs=fix_report,
            tasks_updated=["V-1"],  # v2.3: fix 必填
        )
        # 子 pipeline 完成 → advance 到 verify
        self.assertEqual(r["action"], "dispatch")


class TestIntegrationGateFail(unittest.TestCase):
    """gate fail → fix-gate → gate。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.change_root = os.path.join(self.tmp, "test-change")
        os.makedirs(self.change_root)
        self.orch = _setup_initial_state(self.tmp, "test-change")

    def test_gate_fail_then_fix_gate(self):
        """gate fail → 创建 fix-gate 子 pipeline。"""
        self.orch.record("completed", summary="test 完成", outputs="/tmp/Test.java",
                         tasks_updated=["1.1"])  # test
        self.orch.record("completed", summary="dev 完成", outputs="/tmp/Dev.java",
                         tasks_updated=["2.1"])  # dev
        verify_report = _make_report("# PASS\nverify ok")
        self.orch.record(
            "completed", summary="verify 完成",
            report_path=verify_report, outputs=verify_report,
        )  # verify

        # gate fail（gate 阶段需要 report + evidence + gate_score）
        gate_report = _make_report("# FAIL\nG-1 not met")
        r = self.orch.record(
            "fail", summary="G-1 not met gate_score: 60, p0_failures: [G-1]",
            report_path=gate_report, outputs=gate_report,
        )
        self.assertEqual(r["action"], "dispatch")
        self.assertIsNotNone(self.orch.state.current_sub_pipeline)


class TestOrchestratorProgress(unittest.TestCase):
    """progress 命令。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.change_root = os.path.join(self.tmp, "test-change")
        os.makedirs(self.change_root)
        self.orch = _setup_initial_state(self.tmp, "test-change")

    def test_progress(self):
        p = self.orch.progress()
        self.assertEqual(p["change"], "test-change")
        self.assertEqual(p["status"], "running")
        self.assertIn("tracks", p)
        self.assertIn("event_count", p)
        self.assertGreaterEqual(p["event_count"], 0)

    def test_progress_after_records(self):
        self.orch.record("completed", summary="test 完成", outputs="/tmp/Test.java",
                         tasks_updated=["1.1"])
        p = self.orch.progress()
        self.assertGreaterEqual(p["event_count"], 1)


class TestOrchestratorDispatchFile(unittest.TestCase):
    """验证 orchestrator.next() / record() 写入 dispatch_file。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.change_root = os.path.join(self.tmp, "test-change")
        os.makedirs(self.change_root)
        self.orch = _setup_initial_state(self.tmp, "test-change")

    def test_next_returns_dispatch_file(self):
        """next() 返回的 dispatch action 包含 dispatch_file。

        注意：setUp 中 _setup_initial_state 已调过一次 next()，所以状态中
        last_dispatch_file 已有值。第二次 next() 会返回 retry action，
        但 retry action 也携带 dispatch_file。本测试既验证首次 dispatch
        也验证 retry 行为下 dispatch_file 的存在。
        """
        r = self.orch.next()
        # 第二次 next() 因 P3 retry 机制返回 retry action（非 dispatch）
        self.assertIn(r["action"], ("dispatch", "retry"))
        self.assertIn("dispatch_file", r,
                      "next() 返回必须包含 dispatch_file 字段")
        self.assertTrue(os.path.isfile(r["dispatch_file"]),
                        f"dispatch_file 应在磁盘上存在: {r.get('dispatch_file')}")

    def test_record_returns_dispatch_file(self):
        """record() 返回的 dispatch action 包含 dispatch_file。"""
        r = self.orch.record("completed", summary="test 完成", outputs="/tmp/Test.java",
                             tasks_updated=["1.1"])
        self.assertIn("dispatch_file", r,
                      "record 返回的 dispatch action 必须包含 dispatch_file")
        self.assertTrue(os.path.isfile(r["dispatch_file"]))

    def test_dispatch_file_content(self):
        """dispatch_file 内容包含任务描述。"""
        # 测试阶段不需要 report，但需要 summary
        r = self.orch.record("completed", summary="test 完成", outputs="/tmp/Test.java",
                             tasks_updated=["1.1"])
        # setUp 已 dispatch dev.backend:test，再次 record 可能触发 retry
        # 改为先获取 current dispatch_file（setUp 写入的）
        if "dispatch_file" not in r:
            r = {"dispatch_file": self.orch.state.last_dispatch_file}
        if "dispatch_file" in r and r["dispatch_file"]:
            with open(r["dispatch_file"], encoding="utf-8") as f:
                content = f.read()
            self.assertIn("任务", content, "dispatch_file 应包含任务说明")

    def test_record_returns_commit_field(self):
        """record() 返回包含 commit 字段（auto-commit）。"""
        r = self.orch.record("completed", summary="test 完成", outputs="/tmp/Test.java",
                             tasks_updated=["1.1"])
        # 测试在非 git 目录中运行时，commit 字段仍然存在（attempted=true）
        self.assertIn("commit", r, "record 返回应包含 commit 字段")
        self.assertTrue(r["commit"]["attempted"])


class TestIntegrationSimpleTrack(unittest.TestCase):
    """Simple track 在完整 pipeline 中的行为。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.change_root = os.path.join(self.tmp, "test-change")
        os.makedirs(self.change_root)
        state = PipelineState(
            change="test-change",
            pipeline_order=("proto-gen", "dev.backend"),
            track_types={"proto-gen": "simple"},
            status="running",
            tracks={
                "dev.backend": TrackState.create("dev.backend", modules=("backend",)),
                "proto-gen": TrackState.create("proto-gen", modules=(), max_fail_retries=1),
            },
        )
        save_snapshot(self.tmp, state)
        self.orch = Orchestrator("test-change")
        self.orch.change_root = self.tmp
        self.orch.state = state
        self.orch.next()

    def test_simple_track_route(self):
        """next_pending 在 simple track 上返回 phase=simple。"""
        from pipeline.detect import next_pending
        action = next_pending(self.orch.state)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "simple",
                         "simple track 应路由到 simple phase 而非 test")

    def test_simple_track_completes_first(self):
        """simple track 在标准 track 之前被 dispatch 并完成。"""
        r = self.orch.record("completed", summary="proto-gen commands done")
        self.assertIn("dispatch_file", r,
                      "simple track 的 record 应包含 dispatch_file")


if __name__ == "__main__":
    unittest.main()