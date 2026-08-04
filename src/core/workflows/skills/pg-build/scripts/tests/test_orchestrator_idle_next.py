"""B2+B3: P3 空转检测与 next_step_hint 测试。

覆盖：
- B2: dispatch/retry action 包含 next_step_hint 字段
- B3: idle_next_count 递增（不递增 retry_count）
- B3: 阈值 10 次后 workflow_failed
- B3: record 成功后 idle_next_count 归零
- B3: 新 dispatch 生成时 idle_next_count 归零
- B3: 旧 snapshot 无 idle_next_count 字段向后兼容
- B3: workflow_failed reason 提及 orchestrator idle
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.state import PipelineState, TrackState
from pipeline.orchestrator import Orchestrator
from pipeline.snapshot import save_snapshot


def _setup_dispatched_orchestrator(tmp_root: str, change: str = "test-change") -> Orchestrator:
    """设置已发出首次 dispatch 的 orchestrator（last_dispatch_file 非空）。"""
    state = PipelineState(
        change=change,
        pipeline_order=("dev.backend",),
        status="running",
        tracks={
            "dev.backend": TrackState.create(
                "dev.backend", modules=("backend",), code_review_enabled=True,
            ),
        },
    )
    save_snapshot(tmp_root, state)
    orch = Orchestrator(change)
    orch.change_root = tmp_root
    orch.state = state
    first = orch.next()
    assert first["action"] == "dispatch", f"expected dispatch, got {first['action']}"
    return orch


class TestNextStepHint(unittest.TestCase):
    """B2: dispatch 和 retry action 包含 next_step_hint。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_dispatch_action_contains_next_step_hint(self):
        state = PipelineState(
            change="test-change",
            pipeline_order=("dev.backend",),
            status="running",
            tracks={
                "dev.backend": TrackState.create(
                    "dev.backend", modules=("backend",), code_review_enabled=True,
                ),
            },
        )
        save_snapshot(self.tmp, state)
        orch = Orchestrator("test-change")
        orch.change_root = self.tmp
        orch.state = state
        r = orch.next()
        self.assertEqual(r["action"], "dispatch")
        self.assertEqual(r.get("next_step_hint"), "dispatch_subagent_then_record")

    def test_retry_action_contains_next_step_hint(self):
        orch = _setup_dispatched_orchestrator(self.tmp)
        r = orch.next()
        self.assertEqual(r["action"], "retry")
        self.assertEqual(r.get("next_step_hint"), "dispatch_subagent_then_record")


class TestIdleNextCount(unittest.TestCase):
    """B3: 空转计数与 retry 预算分离。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orch = _setup_dispatched_orchestrator(self.tmp)

    def test_idle_next_increments_idle_count_not_retry_count(self):
        r = self.orch.next()
        self.assertEqual(r["action"], "retry")
        self.assertEqual(r["idle_next_count"], 1)
        self.assertEqual(r["max_idle_next_calls"], 10)
        self.assertNotIn("retry_count", r)
        self.assertNotIn("max_retries", r)
        self.assertEqual(self.orch.state.idle_next_count, 1)
        self.assertEqual(self.orch.state.retry_count, 0)

    def test_idle_next_threshold_10(self):
        for i in range(10):
            r = self.orch.next()
            self.assertEqual(r["action"], "retry", f"call {i+1} should be retry")
            self.assertEqual(r["idle_next_count"], i + 1)
        r = self.orch.next()
        self.assertEqual(r["action"], "workflow_failed")
        self.assertTrue(r["fatal"])

    def test_idle_next_reset_on_record(self):
        for _ in range(3):
            self.orch.next()
        self.assertEqual(self.orch.state.idle_next_count, 3)
        self.orch.record("completed", summary="test 完成", outputs="/tmp/Test.java",
                         tasks_updated=["1.1"])
        self.assertEqual(self.orch.state.idle_next_count, 0)

    def test_idle_next_reset_on_new_dispatch(self):
        for _ in range(2):
            self.orch.next()
        self.assertEqual(self.orch.state.idle_next_count, 2)
        r = self.orch.record("completed", summary="test 完成", outputs="/tmp/Test.java",
                             tasks_updated=["1.1"])
        self.assertEqual(self.orch.state.idle_next_count, 0)
        if r.get("action") == "dispatch":
            self.assertEqual(r.get("next_step_hint"), "dispatch_subagent_then_record")

    def test_workflow_failed_reason_mentions_orchestrator_idle(self):
        for _ in range(10):
            self.orch.next()
        r = self.orch.next()
        self.assertEqual(r["action"], "workflow_failed")
        self.assertIn("idle next() calls", r["reason"])
        self.assertIn("dispatch", r["reason"])

    def test_backward_compat_old_snapshot_no_idle_field(self):
        d = {
            "schema_version": "2026-06-30",
            "change": "old-change",
            "pipeline_order": ["dev.backend"],
            "tracks": {},
            "status": "running",
            "last_dispatch_file": "/some/path.md",
            "retry_count": 2,
        }
        state = PipelineState.from_dict(d)
        self.assertEqual(state.idle_next_count, 0)
        self.assertEqual(state.retry_count, 2)

    def test_to_dict_includes_idle_next_count(self):
        state = PipelineState(change="test", idle_next_count=5)
        d = state.to_dict()
        self.assertEqual(d["idle_next_count"], 5)


if __name__ == "__main__":
    unittest.main()
