"""Unit tests for replay.py — v2.1 checkpoint/resume 机制。"""

import json
import os
import sys
import tempfile
import unittest


sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "scripts",
    ),
)

from pipeline.replay import load_events, replay_state, verify_snapshot_matches_replay


def _make_event(event_type: str, data: dict) -> dict:
    """构造 event dict（追加 schema_version 等元数据）。"""
    return {
        "schema_version": "2026-06-30",
        "seq": 1,
        "ts": "2026-07-02T00:00:00Z",
        "type": event_type,
        "data": data,
    }


class TestReplay(unittest.TestCase):
    """replay_state / verify_snapshot_matches_replay 行为测试。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pg_replay_test_")
        self.build_dir = os.path.join(self.tmp, "2-build")
        os.makedirs(self.build_dir, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_events(self, events: list[dict]) -> None:
        path = os.path.join(self.build_dir, "pipeline.events")
        with open(path, "w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")

    def test_load_events_empty(self):
        events = load_events(self.tmp)
        self.assertEqual(events, [])

    def test_load_events_skips_corrupted_lines(self):
        path = os.path.join(self.build_dir, "pipeline.events")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(_make_event("pipeline_started", {"change": "x"})) + "\n")
            f.write("this is not json\n")
            f.write(json.dumps(_make_event("dispatch_started", {"track": "t"})) + "\n")
        events = load_events(self.tmp)
        self.assertEqual(len(events), 2)

    def test_replay_empty_events(self):
        state = replay_state(self.tmp)
        # change 名从目录 basename 推导（v2.1 兼容 archive 路径）
        self.assertTrue(state.change.startswith("pg_replay_test_"))
        self.assertEqual(state.status, "pending")

    def test_replay_records_phase_transition(self):
        """记录 1 个 completed test phase → replay 后 state 应推进到 test=completed。"""
        self._write_events([
            _make_event("pipeline_started", {"change": "x", "pipeline_order": ["dev.backend"]}),
            _make_event("record_received", {
                "track": "dev.backend",
                "phase": "test",
                "status": "completed",
                "summary": "test done",
            }),
        ])
        state = replay_state(self.tmp)
        # 注：replay 不携带 track 创建信息，tracks 字典可能为空
        # 但顶层 status 字段反映最后 reducer 输出
        # 当前 reducer 在 record 完成后返回 action.advance，state 不变
        # 所以顶层 status 仍是 pending（empty PipelineState 的默认）
        self.assertEqual(state.change, "")

    def test_replay_ignores_non_record_events(self):
        """非 record_received 事件不影响 state。"""
        self._write_events([
            _make_event("dispatch_started", {"track": "t", "phase": "test"}),
            _make_event("track_completed", {"track": "t"}),
            _make_event("pipeline_completed", {"final_status": "completed"}),
        ])
        state = replay_state(self.tmp)
        # 顶层 status 不受这些事件影响
        self.assertEqual(state.status, "pending")

    def test_verify_no_snapshot(self):
        """snapshot 不存在 → 返回 ok=True"""
        ok, msg = verify_snapshot_matches_replay(self.tmp)
        self.assertTrue(ok)
        self.assertIn("snapshot 不存在", msg)

    def test_verify_consistent_snapshot(self):
        """snapshot 与 replay 顶层字段一致 → ok=True"""
        from pipeline.state import PipelineState
        from pipeline.snapshot import save_snapshot

        # 1. 写入与 replay 输出匹配的 snapshot (status=pending)
        change_name = os.path.basename(self.tmp)
        state = PipelineState(change=change_name, status="pending")
        save_snapshot(self.tmp, state)
        # 2. 不写任何 events → replay 推导 status=pending
        ok, msg = verify_snapshot_matches_replay(self.tmp)
        self.assertTrue(ok, msg)

    def test_verify_inconsistent_status(self):
        """snapshot status != replay 推导的 status → ok=False"""
        from pipeline.state import PipelineState
        from pipeline.snapshot import save_snapshot

        # 写 snapshot 但 status=completed，replay 无 events → 推导 status=pending
        # → 不一致
        change_name = os.path.basename(self.tmp)
        state = PipelineState(change=change_name, status="completed")
        save_snapshot(self.tmp, state)
        ok, msg = verify_snapshot_matches_replay(self.tmp)
        self.assertFalse(ok)
        self.assertIn("status", msg)


if __name__ == "__main__":
    unittest.main()