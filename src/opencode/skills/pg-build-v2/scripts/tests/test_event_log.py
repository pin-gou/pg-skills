"""EventLog 单元测试。覆盖 append / replay / tail / filter / last_event。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

# 添加 scripts/ 到 sys.path 以便 import pipeline.*
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.event_log import EventLog


class TestEventLogAppend(unittest.TestCase):
    """append 行为正确性。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = EventLog(change_root=self.tmp)

    def test_append_single_event(self):
        evt = self.log.append("test_event", {"foo": "bar"})
        self.assertEqual(evt["type"], "test_event")
        self.assertEqual(evt["data"]["foo"], "bar")
        self.assertIn("ts", evt)
        self.assertTrue(self.log.exists())
        self.assertFalse(self.log.is_empty())

    def test_append_with_snapshot(self):
        self.log.append(
            "pipeline_started",
            {"change": "test-change"},
            snapshot_after={"status": "running"},
        )
        events = self.log.replay()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["snapshot_after"]["status"], "running")

    def test_append_custom_ts(self):
        evt = self.log.append("test", {}, ts="2026-06-30T10:00:00+08:00")
        self.assertEqual(evt["ts"], "2026-06-30T10:00:00+08:00")

    def test_multiple_appends_in_order(self):
        for i in range(5):
            self.log.append("evt", {"i": i})
        events = self.log.replay()
        self.assertEqual([e["data"]["i"] for e in events], [0, 1, 2, 3, 4])


class TestEventLogReplay(unittest.TestCase):
    """replay / iter_events / count 行为。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = EventLog(change_root=self.tmp)

    def test_replay_empty(self):
        self.assertEqual(self.log.replay(), [])
        self.assertEqual(self.log.count(), 0)

    def test_replay_nonexistent(self):
        log2 = EventLog(change_root=os.path.join(self.tmp, "missing"))
        self.assertEqual(log2.replay(), [])
        self.assertFalse(log2.exists())

    def test_count(self):
        for i in range(10):
            self.log.append("test", {"i": i})
        self.assertEqual(self.log.count(), 10)

    def test_skip_malformed_lines(self):
        """单行损坏不影响其他 event 读取。"""
        self.log.append("valid", {"i": 0})
        # 手动注入损坏行
        path = os.path.join(self.tmp, "2-build", "pipeline.events")
        with open(path, "a") as f:
            f.write("{not valid json\n")
        self.log.append("valid", {"i": 1})
        events = self.log.replay()
        self.assertEqual(len(events), 2)


class TestEventLogTail(unittest.TestCase):
    """tail(N) 倒读最后 N 条。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = EventLog(change_root=self.tmp)

    def test_tail_n_less_than_total(self):
        for i in range(20):
            self.log.append("test", {"i": i})
        tail = self.log.tail(5)
        self.assertEqual([e["data"]["i"] for e in tail], [15, 16, 17, 18, 19])

    def test_tail_more_than_total(self):
        for i in range(3):
            self.log.append("test", {"i": i})
        tail = self.log.tail(10)
        self.assertEqual([e["data"]["i"] for e in tail], [0, 1, 2])

    def test_tail_zero(self):
        self.log.append("test")
        self.assertEqual(self.log.tail(0), [])

    def test_tail_large_log(self):
        """测试大文件 tail（>64KB buffer size）"""
        for i in range(2000):
            self.log.append("test", {"i": i, "padding": "x" * 100})
        tail = self.log.tail(10)
        self.assertEqual([e["data"]["i"] for e in tail], list(range(1990, 2000)))


class TestEventLogQuery(unittest.TestCase):
    """filter_by_type / last_event。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = EventLog(change_root=self.tmp)

    def test_filter_by_type(self):
        self.log.append("dispatch_started", {"track": "backend"})
        self.log.append("record_received", {"status": "completed"})
        self.log.append("dispatch_started", {"track": "frontend"})

        dispatched = self.log.filter_by_type("dispatch_started")
        self.assertEqual(len(dispatched), 2)
        self.assertEqual(dispatched[0]["data"]["track"], "backend")
        self.assertEqual(dispatched[1]["data"]["track"], "frontend")

    def test_last_event(self):
        self.log.append("first", {"i": 0})
        self.log.append("second", {"i": 1})
        last = self.log.last_event()
        assert last is not None  # for type checker
        self.assertEqual(last["type"], "second")
        self.assertEqual(last["data"]["i"], 1)

    def test_last_event_empty(self):
        self.assertIsNone(self.log.last_event())


class TestEventLogPathResolution(unittest.TestCase):
    """路径解析。"""

    def test_explicit_path(self):
        custom = "/tmp/custom-events.jsonl"
        log = EventLog(path=custom)
        log.append("test", {"x": 1})
        self.assertTrue(os.path.isfile(custom))
        os.remove(custom)

    def test_path_or_root_required(self):
        with self.assertRaises(ValueError):
            EventLog()

    def test_default_path(self):
        root = "/tmp/some-change"
        log = EventLog(change_root=root)
        expected = os.path.join(root, "2-build", "pipeline.events")
        self.assertEqual(log.path, expected)


if __name__ == "__main__":
    unittest.main()