"""Bootstrap / Context-chain / Migrate / Git 操作测试。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.event_log import EventLog
from pipeline.context_chain import context_chain_path, init_context_chain, append_event, rebuild_from_events
from pipeline.state import PipelineState
import bootstrap


class TestMigrateLegacyFiles(unittest.TestCase):
    """_migrate_files_impl 测试。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_migrate_creates_2build(self):
        change_root = os.path.join(self.tmp, "test-change")
        os.makedirs(change_root, exist_ok=True)

        moved = bootstrap._migrate_files_impl(change_root)
        self.assertEqual(moved, [])

        apply_dir = os.path.join(change_root, "2-build")
        self.assertTrue(os.path.isdir(apply_dir))

    def test_migrate_moves_legacy_state(self):
        change_root = os.path.join(self.tmp, "test-change")
        os.makedirs(change_root, exist_ok=True)
        legacy = os.path.join(change_root, ".pipeline-state.json")
        with open(legacy, "w") as f:
            json.dump({"version": 1}, f)

        moved = bootstrap._migrate_files_impl(change_root)
        self.assertTrue(any("pipeline-state" in m for m in moved))
        self.assertFalse(os.path.isfile(legacy))
        target = os.path.join(change_root, "2-build", ".pipeline-state.json")
        self.assertTrue(os.path.isfile(target))

    def test_migrate_removes_orphan_files(self):
        change_root = os.path.join(self.tmp, "test-change")
        os.makedirs(change_root, exist_ok=True)
        apply_dir = os.path.join(change_root, "2-build")
        os.makedirs(apply_dir, exist_ok=True)

        # 同时存在 legacy 和 target → 删 legacy
        legacy = os.path.join(change_root, ".pipeline-state.json")
        with open(legacy, "w") as f:
            f.write("{}")
        target = os.path.join(apply_dir, ".pipeline-state.json")
        with open(target, "w") as f:
            f.write('{"v":2}')

        moved = bootstrap._migrate_files_impl(change_root)
        self.assertFalse(os.path.isfile(legacy))
        self.assertTrue(os.path.isfile(target))


class TestContextChain(unittest.TestCase):
    """context_chain.py 测试。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.change_root = os.path.join(self.tmp, "test-change")
        os.makedirs(os.path.join(self.change_root, "2-build"), exist_ok=True)

    def test_init_context_chain(self):
        init_context_chain(self.change_root)
        path = context_chain_path(self.change_root)
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Context Chain", content)

    def test_append_dispatch_started(self):
        init_context_chain(self.change_root)
        append_event(self.change_root, {
            "ts": "2026-06-30T10:00:00+08:00",
            "type": "dispatch_started",
            "data": {"track": "dev.backend", "phase": "test"},
        })
        with open(context_chain_path(self.change_root), encoding="utf-8") as f:
            content = f.read()
        self.assertIn("dev.backend:test", content)

    def test_append_record_received(self):
        init_context_chain(self.change_root)
        append_event(self.change_root, {
            "ts": "2026-06-30T10:05:00+08:00",
            "type": "record_received",
            "data": {"track": "dev.backend", "phase": "test", "status": "completed", "summary": "OK"},
        })
        with open(context_chain_path(self.change_root), encoding="utf-8") as f:
            content = f.read()
        self.assertIn("COMPLETED", content)
        self.assertIn("OK", content)

    def test_append_pipeline_completed(self):
        init_context_chain(self.change_root)
        append_event(self.change_root, {
            "ts": "2026-06-30T11:00:00+08:00",
            "type": "pipeline_completed",
            "data": {"final_status": "completed"},
        })
        with open(context_chain_path(self.change_root), encoding="utf-8") as f:
            content = f.read()
        self.assertIn("PIPELINE COMPLETED", content)

    def test_rebuild_from_events(self):
        events = [
            {"ts": "T1", "type": "pipeline_started", "data": {"change": "test"}},
            {"ts": "T2", "type": "dispatch_started", "data": {"track": "x", "phase": "test"}},
            {"ts": "T3", "type": "record_received", "data": {"track": "x", "phase": "test", "status": "completed"}},
            {"ts": "T4", "type": "pipeline_completed", "data": {"final_status": "completed"}},
        ]
        content = rebuild_from_events(self.change_root, events)
        self.assertIn("PIPELINE STARTED", content)
        self.assertIn("COMPLETED", content)
        self.assertIn("PIPELINE COMPLETED", content)


class TestBootstrapEnvHook(unittest.TestCase):
    """execute_env_hook_inline 测试。"""

    def test_no_project_yaml(self):
        tmp = tempfile.mkdtemp()
        old = os.environ.get("PG_PROJECT_ROOT")
        os.environ["PG_PROJECT_ROOT"] = tmp
        try:
            result = bootstrap.execute_env_hook_inline("test-change", "prepare_env")
            self.assertTrue(result.get("skipped"))
        finally:
            if old:
                os.environ["PG_PROJECT_ROOT"] = old


if __name__ == "__main__":
    unittest.main()