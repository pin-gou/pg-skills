"""Reducer 错误路径无副作用回归测试 (v2.3 Phase 1 修复)。

regression: state must NOT be cleared when reducer returns error.
regression: event_log must NOT be appended.
regression: snapshot must NOT be overwritten with empty state.
regression: caller can retry with valid args.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__) + "/..")

from pipeline.events import (
    PipelineRecord, PipelineAction,
    EVT_RECORD_RECEIVED,
    STATUS_COMPLETED, STATUS_ESCALATE,
)
from pipeline.orchestrator import Orchestrator
from pipeline.reducer import reduce_state
from pipeline.snapshot import load_snapshot, save_snapshot
from pipeline.state import PipelineState, TrackState, PhaseState


class TestReducerErrorStatePreservation(unittest.TestCase):
    """纯 reducer 层：error action 应保留 state。"""

    def test_escalate_without_tasks_updated_keeps_state(self):
        """escalate 缺 tasks_updated → error，state 应原封不动。"""
        # 构造一个 verify 阶段在运行的 state
        verify = PhaseState(status="running", attempt=1)
        track = TrackState.create(
            "dev.backend",
            phases={"test": PhaseState(status="completed"),
                    "dev": PhaseState(status="completed"),
                    "verify": verify},
        )
        state = PipelineState(
            change="x",
            pipeline_order=("dev.backend",),
            tracks={"dev.backend": track},
            current_track="dev.backend",
            current_phase="verify",
            status="running",
        )

        # escalate 但不传 tasks_updated
        record = PipelineRecord(
            track="dev.backend", phase="verify", status=STATUS_ESCALATE,
            summary="missing tasks_updated",
            tasks_updated=(),
        )
        new_state, action = reduce_state(state, record)

        # 关键断言 1: 返回 error action
        self.assertEqual(action.kind, "error")
        self.assertIn("tasks_updated", action.detail.get("reason", ""))

        # 关键断言 2: state 应保留（tracks 仍存在）
        self.assertIn("dev.backend", new_state.tracks,
                       "state 应保留，不能被 reducer 清空")
        self.assertEqual(new_state.status, "running")
        self.assertEqual(new_state.current_track, "dev.backend")
        self.assertEqual(new_state.current_phase, "verify")

        # 关键断言 3: phase 状态应未变
        verify_after = new_state.tracks["dev.backend"].phases["verify"]
        self.assertEqual(verify_after.status, "running",
                          "verify 状态应保留为 running（reducer 应是 no-op）")
        self.assertEqual(len(verify_after.fix_cycles), 0,
                          "reduce 出错时不应创建 fix 子 pipeline")

    def test_unknown_phase_keeps_state(self):
        """未知 phase → error，state 应保留。"""
        state = PipelineState(
            change="x",
            pipeline_order=("dev.backend",),
            tracks={"dev.backend": TrackState.create("dev.backend")},
            current_track="dev.backend",
            current_phase="bogus_phase",
        )

        record = PipelineRecord(
            track="dev.backend", phase="bogus_phase", status=STATUS_COMPLETED,
            summary="unknown phase",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "error")
        self.assertIn("dev.backend", new_state.tracks,
                       "未知 phase 出错时 state 应保留")


class TestOrchestratorErrorPathNoSideEffects(unittest.TestCase):
    """orchestrator 层：reducer error 时不应有副作用。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="error_path_test_")

        # 构造 land on verify phase 的 state
        track = TrackState.create("dev.backend").replace(phases={
            "test": PhaseState(status="completed", attempt=1),
            "dev": PhaseState(status="completed", attempt=1),
            "verify": PhaseState(status="running", attempt=1),
        })
        state = PipelineState(
            change="error-path-test",
            pipeline_order=("dev.backend",),
            tracks={"dev.backend": track},
            current_track="dev.backend",
            current_phase="verify",
            status="running",
            stage_order=("dev",),
            stage_env_map={"dev": "dev-local"},
        )
        save_snapshot(self.tmp, state)
        self.orch = Orchestrator("error-path-test")
        self.orch.change_root = self.tmp
        # 重建 event_log 指向正确路径
        from pipeline.event_log import EventLog
        self.orch.event_log = EventLog(change_root=self.tmp)
        self.orch.state = state

    def test_escalate_without_tasks_updated_no_side_effects(self):
        """verify escalate without tasks_updated → no event log, no snapshot change, no commit."""
        events_before = self.orch.event_log.count()

        verify_report = tempfile.mkstemp(suffix=".md", dir=self.tmp)[1]
        with open(verify_report, "w") as f:
            f.write("# V md\n")

        result = self.orch.record(
            "escalate",
            summary="missing tasks_updated",
            report_path=verify_report,
            outputs=verify_report,
            evidence_paths=[verify_report],
        )

        self.assertEqual(result["action"], "error")
        # v2.3: schema 校验（输入层）→ fatal=True；reducer 错误（业务层）→ fatal=False
        self.assertTrue(result.get("fatal", False),
                        "v2.3: schema 校验错误应 fatal=True（输入层错误必须修正）")

        snapshot_after = load_snapshot(self.tmp)
        self.assertIn("dev.backend", snapshot_after.tracks,
                       "reducer error 时 state 应保留（tracks 不能消失）")
        self.assertEqual(snapshot_after.tracks["dev.backend"].phases["verify"].status,
                          "running",
                          "verify phase 状态应保留为 running")

        events_after = self.orch.event_log.count()
        self.assertEqual(events_before, events_after,
                          "reducer error 时不应写 record_received")

    def test_subsequent_valid_record_proceeds(self):
        """reducer error 后，正确的 record 应能继续推进。"""
        events_before = self.orch.event_log.count()

        verify_report = tempfile.mkstemp(suffix=".md", dir=self.tmp)[1]
        with open(verify_report, "w") as f:
            f.write("# V md\n")

        # 第一次 record：缺 tasks_updated
        result1 = self.orch.record(
            "escalate",
            summary="missing",
            report_path=verify_report,
            outputs=verify_report,
            evidence_paths=[verify_report],
        )
        self.assertEqual(result1["action"], "error")
        events_after_error = self.orch.event_log.count()
        self.assertEqual(events_before, events_after_error,
                          "reducer error 不应写 event log")

        # 第二次 record：带正确的 tasks_updated
        result2 = self.orch.record(
            "escalate",
            summary="with tasks_updated",
            report_path=verify_report,
            outputs=verify_report,
            evidence_paths=[verify_report],
            tasks_updated=["V-1"],
        )
        self.assertEqual(result2["action"], "dispatch",
                          "正确 record 应推进 pipeline")
        self.assertEqual(result2["sub"], "fix",
                          "verify.escalate 后应 dispatch fix")

        events_after_success = self.orch.event_log.count()
        self.assertGreater(events_after_success, events_after_error,
                            "正确 record 后 event log 应增加")


if __name__ == "__main__":
    unittest.main()
