"""集成测试：完整 TDVG 流程（test→dev→verify→gate→pass）。"""

from __future__ import annotations

import os
import json
import shutil
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


def _run_review(orch, summary: str = "review_score: 90, p0_failures: []") -> dict:
    """v2.6: 跑 review 阶段（在 dev 与 verify 之间）。

    集成测试 helper：完成当前 review dispatch，dispatch verify。
    """
    cv_report = _make_report(f"# PASS\n{summary}")
    return orch.record(
        "completed", summary=summary,
        report_path=cv_report, outputs=cv_report,
    )


def _setup_initial_state(tmp_root: str, change: str = "test-change") -> Orchestrator:
    """设置初始 state 并跳过 bootstrap。"""
    state = PipelineState(
        change=change,
        pipeline_order=("dev.backend", "dev.frontend"),
        status="running",
        tracks={
            "dev.backend": TrackState.create(
                "dev.backend", modules=("backend",), code_review_enabled=True,
            ),
            "dev.frontend": TrackState.create(
                "dev.frontend", modules=("frontend",), code_review_enabled=True,
            ),
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
        """test→dev→review→verify→gate→pass→track completed。"""
        track = "dev.backend"

        # Step 1: test completed → dev
        r1 = self.orch.record("completed", summary="10 tests PASS", outputs="/tmp/Test.java",
                              tasks_updated=["1.1"])
        self.assertEqual(r1["action"], "dispatch")
        self.assertEqual(r1["sub"], "dev")

        # Step 2: dev completed → review（v2.6 新增）
        r2 = self.orch.record("completed", summary="impl done", outputs="/tmp/Impl.java",
                              tasks_updated=["2.1"])
        self.assertEqual(r2["action"], "dispatch")
        self.assertEqual(r2["sub"], "review")

        # Step 2.5: review completed → verify（v2.6 新增）
        r2_5 = _run_review(self.orch)
        self.assertEqual(r2_5["action"], "dispatch")
        self.assertEqual(r2_5["sub"], "verify")

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
        _run_review(self.orch)  # review (v2.6)
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
        _run_review(self.orch)  # review (v2.6)
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
        _run_review(self.orch)  # review (v2.6)

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
        _run_review(self.orch)  # review (v2.6)

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
        _run_review(self.orch)  # review (v2.6)
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
                "dev.backend": TrackState.create(
                    "dev.backend", modules=("backend",), code_review_enabled=True,
                ),
                "proto-gen": TrackState.create(
                    "proto-gen", modules=(), max_fail_retries=1, code_review_enabled=False,
                ),
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


class TestIntegrationScenarioTrack(unittest.TestCase):
    """v3.5 新增：scenario track 端到端（无服务 mock）。

    不启实际 backend/frontend/agent（避免环境依赖）；
    通过 mock result.json 落盘绕过 v2.4 强制落盘校验。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.change_root = os.path.join(self.tmp, "test-change")
        os.makedirs(os.path.join(self.change_root, "2-build"), exist_ok=True)
        with open(os.path.join(self.change_root, "scenario-scenario-test.yaml"), "w") as f:
            f.write("scenarios:\n  - scenario_id: S-mock\n    critical: true\n")
        state = PipelineState(
            change="test-change",
            pipeline_order=("real-integration.scenario-test",),
            track_types={"real-integration.scenario-test": "scenario"},
            status="running",
            tracks={
                "real-integration.scenario-test": TrackState(
                    track_id="real-integration.scenario-test",
                    bare="scenario-test",
                    modules=("backend", "frontend", "agent"),
                    max_fix_retries=3,
                    code_review_enabled=False,
                    verify_enabled=False,
                    gate_enabled=False,
                ),
            },
        )
        save_snapshot(self.tmp, state)
        self.orch = Orchestrator("test-change")
        self.orch.change_root = self.tmp
        self.orch.state = state

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_mock_result(self) -> None:
        """为当前 track/phase 写占位 result.json（绕过 v2.4 强制落盘校验）。"""
        track = self.orch.state.current_track
        phase = self.orch.state.current_phase
        if not track or not phase:
            return
        from pipeline.orchestrator import _derive_result_path
        path = _derive_result_path(self.orch.state, track, phase)
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump({"status": "placeholder"}, f)

    def _next_dispatch(self) -> dict:
        """next() 并立即创建 mock result.json。"""
        action = self.orch.next()
        erp = action.get("expected_result_path")
        if erp:
            os.makedirs(os.path.dirname(erp), exist_ok=True)
            with open(erp, "w") as f:
                json.dump({"status": "placeholder"}, f)
        return action

    def _record(self, status, **kwargs):
        """Mock-friendly record：先写 result.json 占位，再走 record。"""
        self._write_mock_result()
        return self.orch.record(status, **kwargs)

    def test_next_pending_dispatches_scenario_prepare(self):
        action = self._next_dispatch()
        self.assertEqual(action.get("action"), "dispatch")
        self.assertEqual(action.get("sub"), "scenario-prepare")
        self.assertEqual(action.get("item"), "real-integration.scenario-test")
        self.assertEqual(action.get("agent"), "pg-build/scenario-prepare")
        self.assertTrue(os.path.isfile(action["dispatch_file"]))
        basename = os.path.basename(action["dispatch_file"])
        self.assertIn("scenario-prepare-dispatch", basename)
        self.assertIn("expected_result_path", action)
        self.assertIn("scenario-prepare-result", action["expected_result_path"])

    def test_prepare_completed_dispatches_scenario_execute(self):
        self._next_dispatch()
        r = self._record(
            "completed",
            summary="all roles ready",
            report_path=self._touch("/tmp/prepare.md"),
            outputs="/tmp/prepare.log",
        )
        self.assertEqual(r.get("action"), "dispatch")
        self.assertEqual(r.get("sub"), "scenario-execute")
        self.assertTrue(os.path.isfile(r["dispatch_file"]))
        self.assertIn("scenario-execute-dispatch", os.path.basename(r["dispatch_file"]))

    def test_execute_completed_track_done(self):
        """scenario-execute.completed → reducer 返回 advance；state 应推进到 track.completed。"""
        from pipeline.reducer import reduce_state
        from pipeline.events import PipelineRecord, STATUS_COMPLETED
        from pipeline.state import SUB_SCENARIO_PREPARE, SUB_SCENARIO_EXECUTE

        # 跳过 orchestrator 的 final-gate retry：直接构造 state 后调 reducer
        state = self.orch.state
        # 模拟 prepare 已完成
        track = state.tracks["real-integration.scenario-test"]
        from pipeline.state import PhaseState
        track = track.replace(
            phases={
                SUB_SCENARIO_PREPARE: PhaseState(status="completed"),
                SUB_SCENARIO_EXECUTE: PhaseState(status="running", attempt=1),
            },
        )
        state = state.replace(
            tracks={**state.tracks, "real-integration.scenario-test": track},
            current_phase=SUB_SCENARIO_EXECUTE,
        )

        record = PipelineRecord(
            track="real-integration.scenario-test",
            phase=SUB_SCENARIO_EXECUTE,
            status=STATUS_COMPLETED,
            summary="all scenarios passed",
            report_path=self._touch("/tmp/exec.md"),
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "advance")
        self.assertEqual(
            new_state.tracks["real-integration.scenario-test"].status,
            "completed",
        )
        self.assertEqual(
            new_state.tracks["real-integration.scenario-test"]
            .phases[SUB_SCENARIO_EXECUTE].status,
            "completed",
        )

    def test_execute_escalate_dispatches_scenario_fix(self):
        self._next_dispatch()
        self._record("completed", summary="ready", report_path=self._touch("/tmp/p.md"))
        self._next_dispatch()  # dispatch scenario-execute
        r = self._record(
            "escalate",
            summary="S-mock failed",
            tasks_updated=["S-mock"],
            report_path=self._touch("/tmp/exec.md"),
        )
        self.assertEqual(r.get("action"), "dispatch")
        self.assertEqual(r.get("sub"), "scenario-fix")
        self.assertTrue(os.path.isfile(r["dispatch_file"]))
        self.assertIn("scenario-fix-dispatch", os.path.basename(r["dispatch_file"]))
        t = self.orch.state.tracks["real-integration.scenario-test"]
        self.assertEqual(len(t.phases["scenario-execute"].fix_cycles), 1)

    def test_fix_completed_returns_to_scenario_execute(self):
        self._next_dispatch()
        self._record("completed", summary="ready", report_path=self._touch("/tmp/p.md"))
        self._next_dispatch()  # dispatch scenario-execute
        self._record(
            "escalate",
            summary="S-mock failed",
            tasks_updated=["S-mock"],
            report_path=self._touch("/tmp/exec.md"),
        )
        sp = self.orch.state.current_sub_pipeline
        self.assertIsNotNone(sp)
        self.assertEqual(sp.kind, "scenario-fix-cycle")
        self._next_dispatch()  # dispatch scenario-fix
        r = self._record(
            "completed",
            summary="fixed",
            tasks_updated=["S-mock"],
            report_path=self._touch("/tmp/fix.md"),
            outputs=self._touch("/tmp/fix-summary.md"),
        )
        self.assertEqual(r.get("action"), "dispatch")
        self.assertEqual(r.get("sub"), "scenario-execute")
        self.assertIsNone(self.orch.state.current_sub_pipeline)
        t = self.orch.state.tracks["real-integration.scenario-test"]
        fix_cycles = t.phases["scenario-execute"].fix_cycles
        self.assertEqual(len(fix_cycles), 1)
        self.assertEqual(fix_cycles[-1]["status"], "completed")

    def test_execute_escalate_no_tasks_updated_error(self):
        self._next_dispatch()
        self._record("completed", summary="ready", report_path=self._touch("/tmp/p.md"))
        self._next_dispatch()  # dispatch scenario-execute
        r = self._record(
            "escalate",
            summary="some summary",
            report_path=self._touch("/tmp/exec.md"),
        )
        # tasks_updated 缺省 → sub_agent_contract 或 reducer 校验失败（任一即可）
        self.assertEqual(r.get("action"), "error")
        reason = r.get("reason", "").lower()
        self.assertTrue(
            "tasks_updated" in reason or "tasks-updated" in reason,
            f"expected tasks_updated in reason, got {r.get('reason')!r}",
        )

    def test_prepare_failed_workflow_failed(self):
        self._next_dispatch()
        r = self._record(
            "failed",
            summary="backend health_check timed out",
            report_path=self._touch("/tmp/prepare.md"),
        )
        self.assertEqual(r.get("action"), "workflow_failed")
        self.assertIn("scenario-prepare", r.get("reason", ""))
        self.assertTrue(r.get("fatal"))

    def test_execute_escalate_exhausted_workflow_failed(self):
        """max_fix_retries=1：第 1 次 escalate → fix；第 2 次 escalate → workflow_failed。"""
        state = self.orch.state.replace(
            tracks={
                **self.orch.state.tracks,
                "real-integration.scenario-test": self.orch.state.tracks[
                    "real-integration.scenario-test"
                ].replace(max_fix_retries=1),
            },
        )
        self.orch.state = state
        self._next_dispatch()
        self._record("completed", summary="ready", report_path=self._touch("/tmp/p.md"))
        self._next_dispatch()  # dispatch execute
        # 第一次 escalate + fix（允许的 1 次 fix cycle）
        self._record(
            "escalate", summary="S-mock failed",
            tasks_updated=["S-mock"], report_path=self._touch("/tmp/exec1.md"),
        )
        self._next_dispatch()  # dispatch fix
        self._record(
            "completed", summary="fixed",
            tasks_updated=["S-mock"], report_path=self._touch("/tmp/fix.md"),
            outputs=self._touch("/tmp/fix.md"),
        )
        self._next_dispatch()  # dispatch execute (re-run)
        # 第二次 escalate → max_fix_retries=1 已耗尽 → workflow_failed
        r = self._record(
            "escalate", summary="S-mock failed again",
            tasks_updated=["S-mock"], report_path=self._touch("/tmp/exec2.md"),
        )
        self.assertEqual(r.get("action"), "workflow_failed")
        self.assertIn("exhausted", r.get("reason", ""))
        self.assertTrue(r.get("fatal"))

    @staticmethod
    def _touch(path: str) -> str:
        """创建空文件（满足 validate_record_args 的 report_required）。"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("# mock\n")
        return path


if __name__ == "__main__":
    unittest.main()