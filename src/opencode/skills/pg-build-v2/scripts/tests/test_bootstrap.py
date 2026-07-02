"""Bootstrap / Migrate / Git 操作测试。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.event_log import EventLog
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