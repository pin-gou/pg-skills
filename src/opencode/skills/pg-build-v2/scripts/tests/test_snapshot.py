"""Snapshot 单元测试。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.event_log import EventLog
from pipeline.snapshot import (
    SNAPSHOT_FILENAME,
    load_snapshot,
    rebuild_from_events,
    save_snapshot,
    snapshot_path,
    write_snapshot_from_log,
)
from pipeline.state import PipelineState, TrackState, PhaseState


class TestSnapshotPath(unittest.TestCase):
    def test_snapshot_path(self):
        path = snapshot_path("/tmp/my-change")
        self.assertEqual(path, "/tmp/my-change/2-build/pipeline.snapshot.json")


class TestSaveLoad(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.change_root = os.path.join(self.tmp, "my-change")
        os.makedirs(self.change_root)

    def test_save_and_load(self):
        state = PipelineState(
            change="my-change",
            feature_branch="feat/pg/my-change",
            init_committed=True,
        )
        save_snapshot(self.change_root, state)

        loaded = load_snapshot(self.change_root)
        assert loaded is not None  # for type checker
        self.assertEqual(loaded.change, "my-change")
        self.assertEqual(loaded.feature_branch, "feat/pg/my-change")
        self.assertTrue(loaded.init_committed)

    def test_load_nonexistent(self):
        loaded = load_snapshot(self.change_root)
        self.assertIsNone(loaded)

    def test_save_overwrites(self):
        state1 = PipelineState(change="x", status="running")
        save_snapshot(self.change_root, state1)
        state2 = PipelineState(change="x", status="completed")
        save_snapshot(self.change_root, state2)
        loaded = load_snapshot(self.change_root)
        assert loaded is not None
        self.assertEqual(loaded.status, "completed")

    def test_atomic_save(self):
        """save 应该原子写入（不留下 .tmp 文件）。"""
        state = PipelineState(change="x")
        save_snapshot(self.change_root, state)
        tmp_path = snapshot_path(self.change_root) + ".tmp"
        self.assertFalse(os.path.exists(tmp_path))


class TestRebuildFromEvents(unittest.TestCase):
    """reducer_fn(state, event) -> (new_state, action)"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.change_root = os.path.join(self.tmp, "my-change")
        os.makedirs(self.change_root)

    def test_rebuild_with_identity_reducer(self):
        """identity reducer：直接把 event 写入 state 便于验证。"""
        log = EventLog(change_root=self.change_root)
        log.append("pipeline_started", {"change": "my-change"})
        log.append("dispatch_started", {"track": "dev.backend"})

        seen = []

        def collector(state, event):
            seen.append(event["type"])
            return state, None

        rebuild_from_events(self.change_root, PipelineState(change="my-change"), collector)
        self.assertEqual(seen, ["pipeline_started", "dispatch_started"])

    def test_rebuild_skips_failed_events(self):
        """reducer 抛异常的 event 不影响后续。"""
        log = EventLog(change_root=self.change_root)
        log.append("ok_1")
        log.append("broken")
        log.append("ok_2")

        def fragile_reducer(state, event):
            if event["type"] == "broken":
                raise ValueError("simulated reducer failure")
            return state, None

        rebuild_from_events(self.change_root, PipelineState(), fragile_reducer)
        # 应该已经消费了 3 个 event 而不抛错

    def test_rebuild_empty_log(self):
        initial = PipelineState(change="x", status="running")
        result = rebuild_from_events(self.change_root, initial, lambda s, e: (s, None))
        self.assertEqual(result.status, "running")

    def test_rebuild_nonexistent_log(self):
        initial = PipelineState(change="x")
        result = rebuild_from_events(self.change_root, initial, lambda s, e: (s, None))
        self.assertEqual(result.change, "x")


class TestWriteSnapshotFromLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.change_root = os.path.join(self.tmp, "x")

    def test_writes_snapshot_file(self):
        log = EventLog(change_root=self.change_root)
        log.append("pipeline_started", {"change": "x"})

        def fake_reducer(state, event):
            if event["type"] == "pipeline_started":
                return state.replace(status="running"), None
            return state, None

        state = write_snapshot_from_log(self.change_root, fake_reducer)
        self.assertEqual(state.status, "running")

        # 验证文件已写入
        loaded = load_snapshot(self.change_root)
        assert loaded is not None
        self.assertEqual(loaded.status, "running")


if __name__ == "__main__":
    unittest.main()