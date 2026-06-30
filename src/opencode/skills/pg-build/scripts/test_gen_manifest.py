#!/usr/bin/env python3
"""Tests for execution-manifest generation and validation."""
import os
import sys
import tempfile
import unittest

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

from pg_pipeline_common import (
    PROJECT_ROOT, CHANGES_DIR, parse_tasks_sections, count_tasks,
)


class TestParseTasksSections(unittest.TestCase):
    """Verify that parse_tasks_sections correctly splits tasks.md into sections."""

    def setUp(self):
        self.tasks_path = os.path.join(tempfile.mkdtemp(), "tasks.md")

    def _write(self, content):
        with open(self.tasks_path, "w", encoding="utf-8") as f:
            f.write(content)

    def test_basic_sections(self):
        content = """# Test tasks

> - **environment 选择**：dev → dev-local

## 1. dev.backend:test - dev 测试先行（unit）

- [ ] 1.1 test task A
- [ ] 1.2 test task B

## 2. dev.backend:dev - 实现开发

- [ ] 2.1 dev task
"""
        self._write(content)
        sections = parse_tasks_sections(self.tasks_path)
        self.assertEqual(len(sections), 2)
        self.assertIn("1. dev.backend:test", sections[0]["section_key"])
        self.assertIn("2. dev.backend:dev", sections[1]["section_key"])
        self.assertIn("test task A", sections[0]["body"])

    def test_code_block_ignores_internal_hashes(self):
        content = """## 1. dev.backend:dev - 实现开发

- [ ] 1.1 code block

```sql
## This is NOT a new section
INSERT INTO t VALUES (1);
```

## 2. dev.backend:verify - 验证
"""
        self._write(content)
        sections = parse_tasks_sections(self.tasks_path)
        self.assertEqual(len(sections), 2)
        # The ## inside the code block should not create a section

    def test_noop_section_body(self):
        content = """## 5. dev.agent:test - dev 测试先行（unit）

- 无

## 6. dev.agent:dev - 实现开发

- 无
"""
        self._write(content)
        sections = parse_tasks_sections(self.tasks_path)
        self.assertEqual(len(sections), 2)
        for sec in sections:
            body_lines = sec["body"].splitlines(keepends=True)
            _, _, all_noop = count_tasks(body_lines)
            self.assertTrue(all_noop, f"section should be noop: {sec['section_key']}")

    def test_simple_track_heading(self):
        content = """## 9. dev.openapi-gen - dev openapi-gen  (simple track: ...)

- 无
"""
        self._write(content)
        sections = parse_tasks_sections(self.tasks_path)
        self.assertEqual(len(sections), 1)
        self.assertIn("simple track", sections[0]["section_key"])

    def test_final_gate_heading(self):
        content = """## 14. final-gate - 最终门控审查

- gate checklist
"""
        self._write(content)
        sections = parse_tasks_sections(self.tasks_path)
        self.assertEqual(len(sections), 1)
        self.assertIn("final-gate", sections[0]["section_key"])


class TestCountTasks(unittest.TestCase):
    """Verify count_tasks differentiates noop vs active sections."""

    def test_empty(self):
        self.assertEqual(count_tasks([]), (0, 0, False))

    def test_all_noop(self):
        lines = ["- 无\n", "- 无\n"]
        self.assertEqual(count_tasks(lines), (0, 0, True))

    def test_mixed_noop_and_task(self):
        lines = ["- 无\n", "- [ ] real task\n"]
        self.assertEqual(count_tasks(lines), (1, 0, False))

    def test_checked_tasks(self):
        lines = ["- [x] done\n", "- [ ] pending\n"]
        self.assertEqual(count_tasks(lines), (1, 1, False))


if __name__ == "__main__":
    unittest.main()