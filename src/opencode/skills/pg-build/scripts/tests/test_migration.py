"""V1 迁移脚本测试。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.event_log import EventLog
from migrations.v1_to_events import migrate, _rebuild_state_from_v1


class TestV1Migration(unittest.TestCase):
    """从旧 v1 .pipeline-state.json 迁移到新 event log。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.change_root = os.path.join(self.tmp, "my-change")
        os.makedirs(os.path.join(self.change_root, "2-build"), exist_ok=True)

    def _write_v1_state(self, data: dict) -> None:
        path = os.path.join(self.change_root, "2-build", ".pipeline-state.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_migrate_empty_v1_state(self):
        """空的 v1 state → 写入 pipeline_started event。"""
        self._write_v1_state({"version": 1})
        result = migrate(self.change_root)
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["events_written"], 1)

    def test_migrate_completed_pipeline(self):
        """v1 completed pipeline → pipeline_completed event。"""
        self._write_v1_state({
            "version": 2,
            "completed": True,
            "pipeline_order": ["dev.backend"],
            "dispatch_history": [
                {"track": "dev.backend", "phase": "test", "started_at": "T1", "result": "completed"},
                {"track": "dev.backend", "phase": "dev", "started_at": "T2", "result": "completed"},
            ],
            "completed_items": ["dev.backend"],
        })
        result = migrate(self.change_root)
        self.assertGreaterEqual(result["events_written"], 6)

        # 验证 event log
        log = EventLog(change_root=self.change_root)
        events = log.replay()
        types = [e["type"] for e in events]
        self.assertIn("pipeline_started", types)
        self.assertIn("pipeline_completed", types)

    def test_migrate_failed_pipeline(self):
        """v1 failed pipeline → workflow_failed event。"""
        self._write_v1_state({
            "version": 2,
            "failed": True,
            "fail_reason": "backend:test failed",
            "pipeline_order": ["dev.backend"],
        })
        result = migrate(self.change_root)
        log = EventLog(change_root=self.change_root)
        events = log.replay()
        types = [e["type"] for e in events]
        self.assertIn("workflow_failed", types)
        failed_evt = [e for e in events if e["type"] == "workflow_failed"][0]
        self.assertEqual(failed_evt["data"]["reason"], "backend:test failed")

    def test_migrate_skip_if_exists(self):
        """event log 已存在 → 跳过迁移。"""
        log = EventLog(change_root=self.change_root)
        log.append("pipeline_started", {"change": "test"})
        self._write_v1_state({"version": 1})

        result = migrate(self.change_root)
        # 只应该有之前写入的 1 个 event
        log2 = EventLog(change_root=self.change_root)
        self.assertEqual(log2.count(), 1)

    def test_migrate_snapshot_rebuild(self):
        """验证 snapshot 被重建。"""
        self._write_v1_state({
            "version": 2,
            "completed": False,
            "pipeline_order": ["dev.backend"],
            "tracks": {
                "dev.backend": {
                    "track_id": "dev.backend",
                    "bare": "backend",
                    "status": "running",
                    "phases": {
                        "test": {"status": "running", "attempt": 1},
                    },
                },
            },
        })
        result = migrate(self.change_root)
        self.assertTrue(result["snapshot_written"])

    def test_migrate_no_state_file(self):
        """无旧 state 文件 → 跳过。"""
        result = migrate(self.change_root)
        self.assertTrue(result["ok"])
        self.assertIn("未找到旧", " ".join(result.get("warnings", [])))


class TestRebuildState(unittest.TestCase):
    """v1 state → v2 PipelineState 重建测试。"""

    def test_rebuild_tracks(self):
        v1 = {
            "tracks": {
                "dev.backend": {
                    "bare": "backend",
                    "status": "running",
                    "phases": {"test": {"status": "completed", "attempt": 1}},
                },
            },
            "completed_items": [],
        }
        state = _rebuild_state_from_v1(v1, "test-change")
        self.assertIn("dev.backend", state.tracks)
        self.assertEqual(state.tracks["dev.backend"].status, "running")

    def test_rebuild_completed_items(self):
        v1 = {
            "completed_items": ["dev.backend", "dev.frontend"],
        }
        state = _rebuild_state_from_v1(v1, "test-change")
        self.assertIn("dev.backend", state.tracks)
        self.assertIn("dev.frontend", state.tracks)
        self.assertEqual(state.tracks["dev.backend"].status, "completed")

    def test_rebuild_failed_reason(self):
        v1 = {"failed": True, "fail_reason": "oops"}
        state = _rebuild_state_from_v1(v1, "test-change")
        self.assertEqual(state.status, "failed")


if __name__ == "__main__":
    unittest.main()