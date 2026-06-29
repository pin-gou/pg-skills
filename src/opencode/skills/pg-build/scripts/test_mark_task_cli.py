#!/usr/bin/env python3
"""test_mark_task_cli.py — Unit tests for the mark-task CLI.

Per build-r plan §3 Step 5 acceptance:
  - mark-task writes state.json (SSOT)
  - mark-task writes tasks.md (derived view, write-through)
  - mark-task is idempotent (re-marking same task_id is a no-op)
  - mark-task exits 2 on bad input (non-integer task_id, missing args)
  - tasks_marked stays sorted/deduped in state.json
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPTS_DIR = "/home/ubuntu/workspace/pg-skills/src/opencode/skills/pg-build/scripts"


def _make_project(change: str, tasks_md: str) -> str:
    """Create a temp project with .pg/project.yaml + tasks.md."""
    tmp = tempfile.mkdtemp(prefix="pg_mark_task_")
    pg_dir = os.path.join(tmp, ".pg")
    os.makedirs(pg_dir)
    with open(os.path.join(pg_dir, "project.yaml"), "w") as f:
        f.write("# stub project for mark-task test\n")
    changes_dir = os.path.join(tmp, ".pg", "changes", change)
    os.makedirs(changes_dir)
    with open(os.path.join(changes_dir, "tasks.md"), "w") as f:
        f.write(tasks_md)
    return tmp


def _run_cli(project_root: str, change: str, *args) -> subprocess.CompletedProcess:
    """Invoke pg_pipeline_state_v2.py CLI from CWD=project_root."""
    cmd = ["python3", os.path.join(SCRIPTS_DIR, "pg_pipeline_state_v2.py"),
           change, *args]
    return subprocess.run(cmd, capture_output=True, text=True,
                          cwd=project_root, timeout=10)


def _read_state(project_root: str, change: str) -> dict:
    state_path = os.path.join(project_root, ".pg", "changes", change,
                                "2-build", ".pipeline-state.json")
    with open(state_path, encoding="utf-8") as f:
        return json.load(f)


def _read_tasks_md(project_root: str, change: str) -> str:
    tasks_path = os.path.join(project_root, ".pg", "changes", change, "tasks.md")
    with open(tasks_path, encoding="utf-8") as f:
        return f.read()


class TestMarkTaskBasic(unittest.TestCase):
    CHANGE = "cli-demo"

    def setUp(self):
        self.tmp = _make_project(self.CHANGE, """# cli-demo Tasks

## 1. dev.backend:test - test stage
- [ ] 1.1 first task
- [ ] 1.2 second task

## 2. dev.backend:dev - dev stage
- [ ] 2.1 dev task
""")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mark_task_writes_state_json(self):
        r = _run_cli(self.tmp, self.CHANGE, "mark-task", "dev.backend", "test", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        state = _read_state(self.tmp, self.CHANGE)
        marked = state["tracks"]["dev.backend"]["phases"]["test"]["tasks_marked"]
        self.assertEqual(marked, [1])

    def test_mark_task_writes_tasks_md(self):
        r = _run_cli(self.tmp, self.CHANGE, "mark-task", "dev.backend", "test", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        content = _read_tasks_md(self.tmp, self.CHANGE)
        # The 1.1 line should now be checked
        self.assertIn("- [x] 1.1 first task", content)
        # 1.2 should remain unchecked
        self.assertIn("- [ ] 1.2 second task", content)

    def test_mark_task_response_json(self):
        r = _run_cli(self.tmp, self.CHANGE, "mark-task", "dev.backend", "test", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        resp = json.loads(r.stdout)
        self.assertEqual(resp["track"], "dev.backend")
        self.assertEqual(resp["phase"], "test")
        self.assertEqual(resp["task_id"], 2)
        self.assertEqual(resp["tasks_marked"], [2])
        self.assertTrue(resp["tasks_md_updated"])

    def test_mark_task_idempotent(self):
        _run_cli(self.tmp, self.CHANGE, "mark-task", "dev.backend", "test", "1")
        r = _run_cli(self.tmp, self.CHANGE, "mark-task", "dev.backend", "test", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        state = _read_state(self.tmp, self.CHANGE)
        marked = state["tracks"]["dev.backend"]["phases"]["test"]["tasks_marked"]
        self.assertEqual(marked, [1])  # not [1, 1]

    def test_mark_task_multiple_in_phase(self):
        _run_cli(self.tmp, self.CHANGE, "mark-task", "dev.backend", "test", "1")
        _run_cli(self.tmp, self.CHANGE, "mark-task", "dev.backend", "test", "2")
        state = _read_state(self.tmp, self.CHANGE)
        marked = state["tracks"]["dev.backend"]["phases"]["test"]["tasks_marked"]
        self.assertEqual(marked, [1, 2])


class TestMarkTaskErrors(unittest.TestCase):
    CHANGE = "cli-errors"

    def setUp(self):
        self.tmp = _make_project(self.CHANGE, """# x

## 1. dev.backend:test
- [ ] 1.1 foo
""")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_args_exits_2(self):
        r = _run_cli(self.tmp, self.CHANGE, "mark-task")
        self.assertEqual(r.returncode, 2, f"want exit 2, got {r.returncode}: {r.stderr}")
        self.assertIn("Usage", r.stderr)

    def test_partial_args_exits_2(self):
        r = _run_cli(self.tmp, self.CHANGE, "mark-task", "dev.backend")
        self.assertEqual(r.returncode, 2)

    def test_non_integer_task_id_exits_2(self):
        r = _run_cli(self.tmp, self.CHANGE, "mark-task", "dev.backend", "test", "abc")
        self.assertEqual(r.returncode, 2)
        self.assertIn("task_id must be integer", r.stderr)

    def test_unknown_subcommand(self):
        r = _run_cli(self.tmp, self.CHANGE, "bogus-subcommand")
        self.assertEqual(r.returncode, 2)


class TestMarkTaskPhases(unittest.TestCase):
    """Verify tasks_marked is partitioned by (track, phase)."""

    CHANGE = "cli-phases"

    def setUp(self):
        self.tmp = _make_project(self.CHANGE, """# x

## 1. dev.backend:test
- [ ] 1.1 a

## 2. dev.backend:dev
- [ ] 2.1 b

## 3. dev.agent:test
- [ ] 1.1 agent-test
""")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mark_different_phases_independently(self):
        _run_cli(self.tmp, self.CHANGE, "mark-task", "dev.backend", "test", "1")
        _run_cli(self.tmp, self.CHANGE, "mark-task", "dev.backend", "dev", "1")
        _run_cli(self.tmp, self.CHANGE, "mark-task", "dev.agent", "test", "1")
        state = _read_state(self.tmp, self.CHANGE)
        self.assertEqual(
            state["tracks"]["dev.backend"]["phases"]["test"]["tasks_marked"], [1])
        self.assertEqual(
            state["tracks"]["dev.backend"]["phases"]["dev"]["tasks_marked"], [1])
        self.assertEqual(
            state["tracks"]["dev.agent"]["phases"]["test"]["tasks_marked"], [1])

    def test_mark_task_md_updates_correct_section(self):
        _run_cli(self.tmp, self.CHANGE, "mark-task", "dev.backend", "test", "1")
        _run_cli(self.tmp, self.CHANGE, "mark-task", "dev.agent", "test", "1")
        content = _read_tasks_md(self.tmp, self.CHANGE)
        # Both 1.1 lines (in different sections) should be checked
        lines_checked = [l for l in content.splitlines() if "[x]" in l and "1.1" in l]
        self.assertEqual(len(lines_checked), 2)
        # The 2.1 in dev phase should remain unchecked
        self.assertIn("- [ ] 2.1 b", content)


class TestMarkTaskMissingSection(unittest.TestCase):
    """Edge case: tasks.md has no section for (track, phase)."""

    CHANGE = "cli-no-section"

    def setUp(self):
        self.tmp = _make_project(self.CHANGE, """# x

## 1. dev.backend:test
- [ ] 1.1 a
""")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mark_task_md_silently_skipped(self):
        """tasks.md write-through is best-effort. State.json still updated."""
        r = _run_cli(self.tmp, self.CHANGE, "mark-task", "dev.backend", "dev", "1")
        self.assertEqual(r.returncode, 0)
        resp = json.loads(r.stdout)
        self.assertFalse(resp["tasks_md_updated"],
                          "tasks_md should not be updated when section missing")
        # State.json should still have the entry
        state = _read_state(self.tmp, self.CHANGE)
        self.assertEqual(
            state["tracks"]["dev.backend"]["phases"]["dev"]["tasks_marked"], [1])


class TestMarkTaskNoTasksMd(unittest.TestCase):
    """If tasks.md doesn't exist, mark-task still writes state.json."""

    CHANGE = "cli-no-tasks"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pg_mark_no_tasks_")
        os.makedirs(os.path.join(self.tmp, ".pg"))
        with open(os.path.join(self.tmp, ".pg", "project.yaml"), "w") as f:
            f.write("# stub\n")
        # No tasks.md created

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mark_task_works_without_tasks_md(self):
        r = _run_cli(self.tmp, self.CHANGE, "mark-task", "dev.backend", "test", "1")
        self.assertEqual(r.returncode, 0)
        state = _read_state(self.tmp, self.CHANGE)
        self.assertEqual(
            state["tracks"]["dev.backend"]["phases"]["test"]["tasks_marked"], [1])


class TestShowAndNext(unittest.TestCase):
    """Smoke tests for --show and --next subcommands."""

    CHANGE = "cli-show"

    def setUp(self):
        self.tmp = _make_project(self.CHANGE, """# x
## 1. dev.backend:test
- [ ] 1.1 a
""")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_show_dumps_state(self):
        r = _run_cli(self.tmp, self.CHANGE, "--show")
        self.assertEqual(r.returncode, 0)
        state = json.loads(r.stdout)
        self.assertEqual(state["version"], 2)
        self.assertEqual(state["change"], self.CHANGE)

    def test_next_with_empty_pipeline(self):
        r = _run_cli(self.tmp, self.CHANGE, "--next")
        self.assertEqual(r.returncode, 0)
        resp = json.loads(r.stdout)
        self.assertIsNone(resp["kind"])


if __name__ == "__main__":
    unittest.main(verbosity=2)