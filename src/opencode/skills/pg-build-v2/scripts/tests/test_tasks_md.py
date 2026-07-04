"""tasks.md 同步模块测试。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.tasks_md import mark_task, mark_phase_completed, mark_tasks_by_ids


class TestMarkTask(unittest.TestCase):
    """单独的 task 勾选测试。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tasks_path = os.path.join(self.tmp, "tasks.md")
        with open(self.tasks_path, "w", encoding="utf-8") as f:
            f.write("# Test\n\n")
            f.write("## 1. dev.backend:test - 后端测试先行\n\n")
            f.write("- [ ] 1.1 test task A\n")
            f.write("- [ ] 1.2 test task B\n\n")
            f.write("## 2. dev.backend:dev - 后端实现\n\n")
            f.write("- [ ] 2.1 dev task C\n")

    def test_mark_existing_task(self):
        changed = mark_task(self.tmp, "dev.backend", "test", 1)
        self.assertTrue(changed)
        with open(self.tasks_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("- [x] 1.1 test task A", content)
        self.assertIn("- [ ] 1.2 test task B", content)  # 未受影响

    def test_mark_second_task(self):
        changed = mark_task(self.tmp, "dev.backend", "test", 2)
        self.assertTrue(changed)
        with open(self.tasks_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[x]", content)
        self.assertIn("1.2", content)

    def test_mark_already_checked(self):
        mark_task(self.tmp, "dev.backend", "test", 1)
        changed = mark_task(self.tmp, "dev.backend", "test", 1)
        self.assertFalse(changed)  # 已勾选，不再重复

    def test_mark_nonexistent_task(self):
        changed = mark_task(self.tmp, "dev.backend", "test", 99)
        self.assertFalse(changed)

    def test_mark_wrong_section(self):
        changed = mark_task(self.tmp, "nonexistent", "test", 1)
        self.assertFalse(changed)

    def test_mark_dev_section(self):
        changed = mark_task(self.tmp, "dev.backend", "dev", 1)
        self.assertTrue(changed)
        with open(self.tasks_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[x]", content)
        self.assertIn("2.1 dev task C", content)


class TestMarkPhaseCompleted(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tasks_path = os.path.join(self.tmp, "tasks.md")
        with open(self.tasks_path, "w", encoding="utf-8") as f:
            f.write("# Test\n\n")
            f.write("## 1. dev.backend:test - 后端测试先行\n\n")
            f.write("- [ ] 1.1 task A\n")
            f.write("- [ ] 1.2 task B\n\n")
            f.write("## 2. dev.backend:dev - 后端实现\n\n")
            f.write("- [ ] 2.1 task C\n")
            f.write("- [ ] 2.2 task D\n")

    def test_mark_all_in_section(self):
        updated = mark_phase_completed(self.tmp, "dev.backend", "test")
        self.assertEqual(updated, 2)
        with open(self.tasks_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[x] 1.1", content)
        self.assertIn("[x] 1.2", content)
        self.assertIn("[ ] 2.1", content)  # 其他 section 不受影响
        self.assertIn("[ ] 2.2", content)

    def test_mark_empty(self):
        """空 tasks.md 返回 0。"""
        tmp2 = tempfile.mkdtemp()
        updated = mark_phase_completed(tmp2, "dev.backend", "test")
        self.assertEqual(updated, 0)


class TestMarkTasksByIds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tasks_path = os.path.join(self.tmp, "tasks.md")
        with open(self.tasks_path, "w", encoding="utf-8") as f:
            f.write("# Test\n\n")
            f.write("## 1. dev.backend:test - 后端测试先行\n\n")
            f.write("- [ ] 1.1 task A\n")
            f.write("- [ ] 1.2 task B\n")
            f.write("- [ ] 1.3 task C\n\n")
            f.write("## 2. dev.backend:dev - 后端实现\n\n")
            f.write("- [ ] 2.1 task D\n")

    def test_mark_specific_ids(self):
        """只勾指定的 task_id，其他保持未勾。"""
        updated = mark_tasks_by_ids(self.tmp, "dev.backend", "test", ["1.1", "1.3"])
        self.assertEqual(updated, 2)
        with open(self.tasks_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[x] 1.1", content)
        self.assertIn("[ ] 1.2", content)
        self.assertIn("[x] 1.3", content)

    def test_mark_non_numeric_ids_skipped(self):
        """V-* 等非 X.Y 格式静默跳过。"""
        updated = mark_tasks_by_ids(self.tmp, "dev.backend", "test", ["V-backend-1", "1.1"])
        self.assertEqual(updated, 1)
        with open(self.tasks_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[x] 1.1", content)

    def test_mark_empty_ids(self):
        updated = mark_tasks_by_ids(self.tmp, "dev.backend", "test", [])
        self.assertEqual(updated, 0)

    def test_mark_wrong_section(self):
        """在其他 section 的 task_id 不影响。"""
        mark_tasks_by_ids(self.tmp, "dev.backend", "test", ["2.1"])
        with open(self.tasks_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[ ] 2.1", content)  # 2.1 在 dev section，不在 test section


if __name__ == "__main__":
    unittest.main()