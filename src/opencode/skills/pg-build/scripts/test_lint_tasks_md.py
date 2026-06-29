#!/usr/bin/env python3
"""test_lint_tasks_md.py — Unit tests for the CI lint script.

Per build-r plan §3 Step 5 + §9.9:
  - Default rule: forbidden direct checkbox toggle (- [ ] → - [x])
  - Bypass: new task additions (- [ ] X.Y new desc) allowed
  - Bypass: unchecking (- [x] → - [ ]) allowed
  - Bypass: different description = different task
  - Exit 0: clean / bypass cases
  - Exit 1: violations found
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import json


SCRIPTS_DIR = "/home/ubuntu/workspace/pg-skills/src/opencode/skills/pg-build/scripts"


def _git(cwd: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=test", *args],
        capture_output=True, text=True, cwd=cwd, timeout=10)


def _make_repo(tasks_md: str) -> tuple:
    """Create a git repo with tasks.md. Returns (tmp_dir, tasks_path)."""
    tmp = tempfile.mkdtemp(prefix="pg_lint_test_")
    os.makedirs(os.path.join(tmp, ".pg"))
    with open(os.path.join(tmp, ".pg", "project.yaml"), "w") as f:
        f.write("# stub\n")
    changes_dir = os.path.join(tmp, ".pg", "changes", "lint-demo")
    os.makedirs(changes_dir)
    tasks_path = os.path.join(changes_dir, "tasks.md")
    with open(tasks_path, "w") as f:
        f.write(tasks_md)
    _git(tmp, "init", "-q")
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-qm", "initial")
    return tmp, tasks_path


def _run_lint(repo_dir: str, tasks_path: str, *flags) -> subprocess.CompletedProcess:
    cmd = ["python3", os.path.join(SCRIPTS_DIR, "lint_tasks_md.py"),
           *flags, tasks_path]
    return subprocess.run(cmd, capture_output=True, text=True,
                          cwd=repo_dir, timeout=10)


def _toggle_task(tasks_path: str, line_pattern: str):
    """sed-style: replace `- [ ] <line_pattern>` with `- [x] <line_pattern>`."""
    with open(tasks_path, encoding="utf-8") as f:
        content = f.read()
    new_content = content.replace(f"- [ ] {line_pattern}",
                                    f"- [x] {line_pattern}", 1)
    if new_content == content:
        raise RuntimeError(f"pattern not found: {line_pattern}")
    with open(tasks_path, "w", encoding="utf-8") as f:
        f.write(new_content)


class TestLintViolations(unittest.TestCase):
    """Forbidden direct toggle → exit 1."""

    def setUp(self):
        self.tmp, self.tasks = _make_repo(
            "# demo\n\n## 1. dev.backend:test\n"
            "- [ ] 1.1 first task\n"
            "- [ ] 1.2 second task\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_staged_toggle_detected(self):
        _toggle_task(self.tasks, "1.1 first task")
        _git(self.tmp, "add", "-A")
        r = _run_lint(self.tmp, self.tasks)
        self.assertEqual(r.returncode, 1)
        self.assertIn("violation", r.stderr)
        self.assertIn("1.1", r.stderr)
        self.assertIn("mark-task", r.stderr)

    def test_unstaged_toggle_detected(self):
        _toggle_task(self.tasks, "1.2 second task")
        # don't git add — unstaged
        r = _run_lint(self.tmp, self.tasks)
        self.assertEqual(r.returncode, 1)
        self.assertIn("1.2", r.stderr)

    def test_clean_state_exits_0(self):
        r = _run_lint(self.tmp, self.tasks)
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")


class TestLintBypass(unittest.TestCase):
    """Cases that should NOT be flagged."""

    def setUp(self):
        self.tmp, self.tasks = _make_repo(
            "# demo\n\n## 1. dev.backend:test\n"
            "- [ ] 1.1 first task\n"
            "- [ ] 1.2 second task\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_new_task_addition_allowed(self):
        """Adding a new `- [ ]` line is NOT a violation."""
        with open(self.tasks, "a") as f:
            f.write("- [ ] 1.3 third task\n")
        _git(self.tmp, "add", "-A")
        r = _run_lint(self.tmp, self.tasks)
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")

    def test_uncheck_allowed(self):
        """Reverting - [x] → - [ ] is NOT a violation (only forward toggle is)."""
        # First make 1.1 checked and commit
        _toggle_task(self.tasks, "1.1 first task")
        _git(self.tmp, "add", "-A")
        _git(self.tmp, "commit", "-qm", "checked")
        # Then uncheck
        with open(self.tasks, encoding="utf-8") as f:
            content = f.read()
        new_content = content.replace("- [x] 1.1", "- [ ] 1.1")
        with open(self.tasks, "w", encoding="utf-8") as f:
            f.write(new_content)
        _git(self.tmp, "add", "-A")
        r = _run_lint(self.tmp, self.tasks)
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")

    def test_different_description_not_a_toggle(self):
        """Replacing `- [ ] 1.1 old desc` with `- [x] 1.1 NEW desc` is OK
        because the description differs — it's a content edit, not just a
        checkbox toggle. (Edge case: lint shouldn't false-positive.)"""
        with open(self.tasks, encoding="utf-8") as f:
            content = f.read()
        # Remove old line, add new line (with checked + new description)
        new_content = content.replace(
            "- [ ] 1.1 first task\n",
            "+ [x] 1.1 NEW description\n",
        )
        # Add the new line at the end of the section
        with open(self.tasks, "w", encoding="utf-8") as f:
            f.write(new_content)
        # Actually for the test, we need a proper diff. Let me do this differently:
        with open(self.tasks, encoding="utf-8") as f:
            content = f.read()
        # Replace one line's description while keeping the same checkbox state
        # i.e. - [ ] 1.1 first task → - [x] 1.1 first task (toggle, IS violation)
        # vs   - [ ] 1.1 first task → - [x] 1.1 NEW (different desc, NOT a toggle)
        # We'll write the second case by manually editing:
        lines = content.splitlines()
        new_lines = []
        for line in lines:
            if line == "- [ ] 1.1 first task":
                # First remove this line (the original - [ ])
                continue
            new_lines.append(line)
        new_lines.append("- [x] 1.1 NEW description")
        with open(self.tasks, "w", encoding="utf-8") as f:
            f.write("\n".join(new_lines) + "\n")
        _git(self.tmp, "add", "-A")
        r = _run_lint(self.tmp, self.tasks)
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")


class TestLintMultipleSections(unittest.TestCase):
    def setUp(self):
        self.tmp, self.tasks = _make_repo(
            "# demo\n\n"
            "## 1. dev.backend:test\n- [ ] 1.1 a\n\n"
            "## 2. dev.backend:dev\n- [ ] 2.1 b\n\n"
            "## 3. dev.backend:verify\n- [ ] 3.1 c\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_multiple_violations_all_reported(self):
        # Toggle two tasks in different sections
        _toggle_task(self.tasks, "1.1 a")
        _toggle_task(self.tasks, "2.1 b")
        _git(self.tmp, "add", "-A")
        r = _run_lint(self.tmp, self.tasks)
        self.assertEqual(r.returncode, 1)
        self.assertIn("1.1", r.stderr)
        self.assertIn("2.1", r.stderr)


class TestLintStateCrossCheck(unittest.TestCase):
    """Lint cross-references tasks.md toggles against state.json.

    Toggles corresponding to `phases.<phase>.tasks_marked` in state.json
    are legitimate CLI writes and should NOT be flagged.

    Toggles NOT in state.json are violations.
    """

    def _make_repo_with_state(self, tasks_md: str, state: dict) -> tuple:
        tmp = _make_repo.__wrapped__(tasks_md) if hasattr(_make_repo, '__wrapped__') else None
        # Inline the repo creation since _make_repo doesn't take state
        import tempfile
        tmp = tempfile.mkdtemp(prefix="pg_lint_state_")
        os.makedirs(os.path.join(tmp, ".pg"))
        with open(os.path.join(tmp, ".pg", "project.yaml"), "w") as f:
            f.write("# stub\n")
        changes_dir = os.path.join(tmp, ".pg", "changes", "lint-demo")
        os.makedirs(changes_dir)
        tasks_path = os.path.join(changes_dir, "tasks.md")
        with open(tasks_path, "w") as f:
            f.write(tasks_md)
        build_dir = os.path.join(changes_dir, "2-build")
        os.makedirs(build_dir)
        with open(os.path.join(build_dir, ".pipeline-state.json"), "w") as f:
            json.dump(state, f)
        _git(tmp, "init", "-q")
        _git(tmp, "add", "-A")
        _git(tmp, "commit", "-qm", "initial")
        return tmp, tasks_path

    def test_toggle_with_state_match_is_clean(self):
        """tasks_marked contains sub-task → toggle in tasks.md is OK."""
        state = {
            "version": 2,
            "change": "lint-demo",
            "tracks": {
                "dev.backend": {
                    "phases": {
                        "test": {"tasks_marked": [1, 2]},
                    },
                },
            },
        }
        tmp, tasks_path = self._make_repo_with_state(
            "# x\n\n## 1. dev.backend:test\n- [ ] 1.1 a\n- [ ] 1.2 b\n",
            state)
        try:
            # Toggle both 1.1 and 1.2 (legitimate CLI writes)
            _toggle_task(tasks_path, "1.1 a")
            _toggle_task(tasks_path, "1.2 b")
            _git(tmp, "add", "-A")
            r = _run_lint(tmp, tasks_path)
            self.assertEqual(r.returncode, 0,
                              f"want clean, got violations: {r.stderr}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_toggle_without_state_match_is_violation(self):
        """tasks_marked missing sub-task → toggle in tasks.md is violation."""
        state = {
            "version": 2,
            "change": "lint-demo",
            "tracks": {
                "dev.backend": {
                    "phases": {
                        # 1.1 NOT in tasks_marked
                        "test": {"tasks_marked": [2]},
                    },
                },
            },
        }
        tmp, tasks_path = self._make_repo_with_state(
            "# x\n\n## 1. dev.backend:test\n- [ ] 1.1 a\n- [ ] 1.2 b\n",
            state)
        try:
            _toggle_task(tasks_path, "1.1 a")
            _toggle_task(tasks_path, "1.2 b")
            _git(tmp, "add", "-A")
            r = _run_lint(tmp, tasks_path)
            self.assertEqual(r.returncode, 1)
            # 1.1 should be flagged (not in state), 1.2 should pass (in state)
            self.assertIn("1.1", r.stderr)
            self.assertNotIn("1.2", r.stderr,
                                "1.2 should be bypassed by state.json match")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_partial_state_match_flags_only_unmatched(self):
        """Only flag toggles whose sub-task is missing from state.json."""
        state = {
            "version": 2,
            "tracks": {
                "dev.backend": {
                    "phases": {
                        "test": {"tasks_marked": [1]},
                    },
                },
            },
        }
        tmp, tasks_path = self._make_repo_with_state(
            "# x\n\n## 1. dev.backend:test\n"
            "- [ ] 1.1 a\n- [ ] 1.2 b\n- [ ] 1.3 c\n",
            state)
        try:
            _toggle_task(tasks_path, "1.1 a")
            _toggle_task(tasks_path, "1.2 b")
            _toggle_task(tasks_path, "1.3 c")
            _git(tmp, "add", "-A")
            r = _run_lint(tmp, tasks_path)
            self.assertEqual(r.returncode, 1)
            # Only 1.2 and 1.3 should be flagged; 1.1 is in state
            self.assertIn("1.2", r.stderr)
            self.assertIn("1.3", r.stderr)
            self.assertNotIn("(1.1):", r.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestLintDiffDirect(unittest.TestCase):
    """Test diff_toggle_pairs directly without git."""

    def setUp(self):
        sys.path.insert(0, SCRIPTS_DIR)
        from lint_tasks_md import diff_toggle_pairs

    def tearDown(self):
        sys.path.remove(SCRIPTS_DIR)

    def test_toggle_pair_detected(self):
        from lint_tasks_md import diff_toggle_pairs
        diff = (
            "@@ -1,5 +1,5 @@\n"
            " # demo\n"
            " ## 1. dev.backend:test\n"
            "- [ ] 1.1 first task\n"
            "+ [x] 1.1 first task\n"
            " - [ ] 1.2 second task\n"
        )
        toggles = diff_toggle_pairs(diff)
        self.assertEqual(len(toggles), 1)
        section, sub, _, desc = toggles[0]
        self.assertEqual((section, sub), (1, 1))
        self.assertIn("first task", desc)

    def test_new_task_not_a_toggle(self):
        from lint_tasks_md import diff_toggle_pairs
        diff = (
            "@@ -1,4 +1,5 @@\n"
            " # demo\n"
            " ## 1. dev.backend:test\n"
            " - [ ] 1.1 first task\n"
            "+- [ ] 1.3 third task\n"
        )
        toggles = diff_toggle_pairs(diff)
        self.assertEqual(toggles, [])

    def test_uncheck_not_a_violation(self):
        from lint_tasks_md import diff_toggle_pairs
        diff = (
            "@@ -1,4 +1,4 @@\n"
            " # demo\n"
            " ## 1. dev.backend:test\n"
            "- [x] 1.1 first task\n"
            "+ [ ] 1.1 first task\n"
        )
        toggles = diff_toggle_pairs(diff)
        self.assertEqual(toggles, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)