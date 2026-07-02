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


class TestCliBootstrap(unittest.TestCase):
    """cli_bootstrap / cli_env_action 测试。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old_root = os.environ.get("PG_PROJECT_ROOT")
        os.environ["PG_PROJECT_ROOT"] = self.tmp
        self.change_root = os.path.join(self.tmp, ".pg", "changes", "test-change")
        os.makedirs(os.path.join(self.change_root, "2-build"), exist_ok=True)

    def tearDown(self):
        if self.old_root:
            os.environ["PG_PROJECT_ROOT"] = self.old_root
        else:
            os.environ.pop("PG_PROJECT_ROOT", None)

    def test_cli_bootstrap_structure(self):
        """cli_bootstrap 返回正确结构 (无项目配置时跳过 env hook)。"""
        result = bootstrap.cli_bootstrap("test-change")
        self.assertEqual(result["action"], "bootstrap_result")
        self.assertIn("ok", result)
        self.assertIn("init_commit", result)
        self.assertIn("env_hook", result)
        self.assertIn("pipeline_config", result)
        # 无 project.yaml → skipped
        if result.get("env_hook"):
            self.assertTrue(result["env_hook"].get("skipped", False))

    def test_cli_env_action_structure(self):
        """cli_env_action 返回正确结构。"""
        result = bootstrap.cli_env_action("test-change", "prepare_env", "dev", "dev-local")
        self.assertEqual(result["action"], "env_action_result")
        self.assertIn("ok", result)
        self.assertEqual(result["phase"], "prepare_env")
        self.assertEqual(result["stage"], "dev")
        self.assertEqual(result["env_name"], "dev-local")

    def test_cli_env_action_clean_env(self):
        """cli_env_action 支持 clean_env phase。"""
        result = bootstrap.cli_env_action("test-change", "clean_env", "integration", "dev-3tier")
        self.assertEqual(result["action"], "env_action_result")
        self.assertEqual(result["phase"], "clean_env")

    def test_cli_bootstrap_detect_config_no_manifest(self):
        """无 manifest 时 pipeline_config 为默认值。"""
        result = bootstrap.cli_bootstrap("test-change")
        pc = result.get("pipeline_config", {})
        self.assertIn("pipeline_order", pc)
        self.assertIn("track_configs", pc)
        self.assertIn("stage_order", pc)
        self.assertIn("stage_env_map", pc)
        # stage_order 至少非空（具体值取决于运行环境的 project.yaml）
        self.assertGreater(len(pc["stage_order"]), 0)


if __name__ == "__main__":
    unittest.main()