#!/usr/bin/env python3
"""Unit tests for cmd_gate_rollback.

Covers:
- Standard scenario: gate report with 关联 task fields → partial rollback
- Multi-task scenario: 任务 2.3, 任务 2.7 both rolled back
- Fallback 1: report missing → full track rollback
- Fallback 2: report without 关联 task fields → full track rollback
- Cross-track isolation: only parses current track's G-N sections
- Non-matching task IDs are not rolled back
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "scripts"
)
STATE_PY = os.path.join(SCRIPTS_DIR, "pg-pipeline-state.py")


def run_state(*args, cwd=None):
    """Run pg-pipeline-state.py and return parsed JSON."""
    env = os.environ.copy()
    if cwd is not None:
        env["PG_PROJECT_ROOT"] = cwd
    result = subprocess.run(
        [sys.executable, STATE_PY, *args],
        capture_output=True, text=True, cwd=cwd, env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"state script failed: {result.stderr}")
    if result.stderr:
        print(f"[SUBPROC-STDERR] {result.stderr}", flush=True)
    return json.loads(result.stdout)


def make_tasks_md(change_dir, items):
    """Create a tasks.md with given sections.

    items: list of (item, sub, task_count)
    """
    lines = [f"# Tasks - test\n\n"]
    section_num = 1
    for item, sub, task_count in items:
        lines.append(f"## {section_num}. {item}:{sub} - test section\n\n")
        for i in range(1, task_count + 1):
            lines.append(f"- [{'x' if i <= 2 else ' '}] {section_num}.{i} task {section_num}.{i}\n")
        lines.append("\n")
        section_num += 1
    with open(os.path.join(change_dir, "tasks.md"), "w") as f:
        f.writelines(lines)


def make_gate_report(change_dir, gaps, track="test-track"):
    """Create a gate-assessment report file.

    gaps: list of dicts with keys: id, task_ref, file_pos, expected, actual
    track: track id used in the G-N heading (e.g. "test-track")
    """
    lines = [f"# Gate Assessment - {track}\n\n"]
    lines.append("## 不通过项详细说明\n\n")
    for gap in gaps:
        lines.append(f"### {track}:G-{gap['id']} — {gap.get('title', 'gap ' + str(gap['id']))}\n")
        lines.append(f"- **检查项**: #1\n")
        lines.append(f"- **预期**: {gap.get('expected', 'X')}\n")
        lines.append(f"- **实际**: {gap.get('actual', 'Y')}\n")
        lines.append(f"- **文件位置**: {gap.get('file_pos', 'foo.go:1')}\n")
        lines.append(f"- **关联 task**: {gap['task_ref']}\n")
        if gap.get('fix_hint'):
            lines.append(f"- **修复建议**: {gap['fix_hint']}\n")
        lines.append("\n")
    path = os.path.join(change_dir, f"gate-assessment-{track}.md")
    with open(path, "w") as f:
        f.writelines(lines)
    return path


class TestGateRollback(unittest.TestCase):
    def setUp(self):
        # Create isolated temp project root
        self.tmpdir = tempfile.mkdtemp(prefix="test-gate-rollback-")
        self.pg_spec = os.path.join(self.tmpdir, "pg-spec")
        self.changes = os.path.join(self.pg_spec, "changes")
        self.change = "test-change"
        self.change_dir = os.path.join(self.changes, self.change)
        os.makedirs(self.change_dir)

        # Create v3.0 config.yaml so state script can find it
        with open(os.path.join(self.pg_spec, "config.yaml"), "w") as f:
            f.write(
                "schema: spec-driven\n"
                "modules:\n"
                "  test-mod:\n"
                "    root: /tmp\n"
                "    language: python\n"
                "    test:\n"
                "      unit: 'true'\n"
                "tracks:\n"
                "  test-track:\n"
                "    modules: [test-mod]\n"
                "    max_fix_retries: 5\n"
                "    fix_routing: source\n"
                "    review_level: none\n"
                "stages:\n"
                "  - name: dev-isolated\n"
                "    tracks: [test-track]\n"
                "    test_key: unit\n"
                "    environment:\n"
                "      required: false\n"
                "    gate: all_pass\n"
            )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _tasks_path(self):
        return os.path.join(self.change_dir, "tasks.md")

    def _read_tasks_lines(self):
        with open(self._tasks_path()) as f:
            return f.readlines()

    def _count_checked(self, item, sub):
        """Count checked [x] tasks in section {item}:{sub}."""
        lines = self._read_tasks_lines()
        in_section = False
        count = 0
        target = f"{item}:{sub}"
        for line in lines:
            s = line.strip()
            if s.startswith("## "):
                if target in s:
                    in_section = True
                    count = 0
                elif in_section:
                    break
            elif in_section and s.startswith("- [x]"):
                count += 1
        return count

    # --- Test cases ---

    def test_standard_partial_rollback(self):
        """Standard scenario: gate report with 关联 task → only that task rolled back."""
        make_tasks_md(self.change_dir, [
            ("test-track", "dev", 5),  # 2 checked, 3 unchecked
        ])
        report_path = make_gate_report(self.change_dir, [
            {"id": 1, "task_ref": "test-track:dev 任务 1.3"},
        ])

        # Initial: 2 checked
        self.assertEqual(self._count_checked("test-track", "dev"), 2)

        result = run_state("gate-rollback", self.change, "test-track", report_path,
                          cwd=self.tmpdir)

        # task 1.3 was unchecked initially, rollback should not affect it
        # task 1.1 and 1.2 were checked, only 1.1-1.2 minus intersection with 1.3 = 2 checked
        # Actually task 1.3 was unchecked so no rollback happens
        self.assertEqual(result["mode"], "partial")
        self.assertEqual(result["tasksRolledBack"], 0)

    def test_partial_rollback_rolls_back_referenced_task(self):
        """When 关联 task matches a checked task, it gets rolled back."""
        make_tasks_md(self.change_dir, [
            ("test-track", "dev", 5),
        ])
        report_path = make_gate_report(self.change_dir, [
            {"id": 1, "task_ref": "test-track:dev 任务 1.1"},
        ])

        # task 1.1 was checked (first 2 are checked)
        before = self._count_checked("test-track", "dev")
        self.assertEqual(before, 2)

        result = run_state("gate-rollback", self.change, "test-track", report_path,
                          cwd=self.tmpdir)

        after = self._count_checked("test-track", "dev")
        self.assertEqual(after, 1)
        self.assertEqual(result["mode"], "partial")
        self.assertEqual(result["tasksRolledBack"], 1)

    def test_multi_task_rollback(self):
        """Multiple task refs in 关联 task → all rolled back."""
        make_tasks_md(self.change_dir, [
            ("test-track", "dev", 5),
        ])
        report_path = make_gate_report(self.change_dir, [
            {"id": 1, "task_ref": "test-track:dev 任务 1.1, 任务 1.2"},
        ])

        result = run_state("gate-rollback", self.change, "test-track", report_path,
                          cwd=self.tmpdir)

        after = self._count_checked("test-track", "dev")
        # Both 1.1 and 1.2 were checked, both rolled back
        self.assertEqual(after, 0)
        self.assertEqual(result["tasksRolledBack"], 2)

    def test_fallback_when_report_missing(self):
        """Report file does not exist → full track rollback."""
        make_tasks_md(self.change_dir, [
            ("test-track", "dev", 5),
        ])
        nonexistent = os.path.join(self.change_dir, "does-not-exist.md")

        result = run_state("gate-rollback", self.change, "test-track", nonexistent,
                          cwd=self.tmpdir)

        after = self._count_checked("test-track", "dev")
        self.assertEqual(after, 0)  # full rollback

    def test_fallback_when_no_associated_task_field(self):
        """Report has G-N sections but no 关联 task field → full track rollback."""
        make_tasks_md(self.change_dir, [
            ("test-track", "dev", 5),
        ])

        # Build report manually without 关联 task field
        report_path = os.path.join(self.change_dir, "gate-assessment-test.md")
        with open(report_path, "w") as f:
            f.write("""# Gate Assessment

## 不通过项详细说明

### test:G-1 — gap without 关联 task
- **检查项**: #1
- **预期**: X
- **实际**: Y
- **文件位置**: foo.go:1
""")

        result = run_state("gate-rollback", self.change, "test-track", report_path,
                          cwd=self.tmpdir)

        after = self._count_checked("test-track", "dev")
        self.assertEqual(after, 0)  # full rollback because no 关联 task found

    def test_cross_track_isolation(self):
        """G-N sections from other tracks should not affect current track."""
        make_tasks_md(self.change_dir, [
            ("test-track", "dev", 5),
            ("other-track", "dev", 5),
        ])
        # Initial: each track has 2 checked
        self.assertEqual(self._count_checked("test-track", "dev"), 2)
        self.assertEqual(self._count_checked("other-track", "dev"), 2)

        # Report has G-N from BOTH tracks
        report_path = os.path.join(self.change_dir, "gate-assessment-test.md")
        with open(report_path, "w") as f:
            f.write("""# Gate Assessment

## 不通过项详细说明

### test-track:G-1 — gap in test-track
- **检查项**: #1
- **预期**: X
- **实际**: Y
- **文件位置**: foo.go:1
- **关联 task**: test-track:dev 任务 1.1

### other-track:G-1 — gap in other-track
- **检查项**: #1
- **预期**: X
- **实际**: Y
- **文件位置**: bar.go:1
- **关联 task**: other-track:dev 任务 1.1
""")

        run_state("gate-rollback", self.change, "test-track", report_path,
                 cwd=self.tmpdir)

        # test-track task 1.1 rolled back, other-track untouched
        self.assertEqual(self._count_checked("test-track", "dev"), 1)
        self.assertEqual(self._count_checked("other-track", "dev"), 2)  # unchanged

    def test_non_matching_task_id_not_rolled_back(self):
        """关联 task referencing a non-existent task ID is ignored."""
        make_tasks_md(self.change_dir, [
            ("test-track", "dev", 5),
        ])

        report_path = make_gate_report(self.change_dir, [
            {"id": 1, "task_ref": "test-track:dev 任务 9.9"},  # doesn't exist
        ])

        before = self._count_checked("test-track", "dev")
        result = run_state("gate-rollback", self.change, "test-track", report_path,
                          cwd=self.tmpdir)

        after = self._count_checked("test-track", "dev")
        self.assertEqual(after, before)  # nothing rolled back
        self.assertEqual(result["tasksRolledBack"], 0)


class TestApplyDirLayout(unittest.TestCase):
    """Verify pg-build artifacts live under <change>/2-build/."""

    def setUp(self):
        import importlib.util
        runner_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "pg-pipeline-runner.py"
        )
        spec = importlib.util.spec_from_file_location(
            "pg_pipeline_runner", runner_path
        )
        assert spec is not None
        self.runner = importlib.util.module_from_spec(spec)  # type: ignore[assignment]
        assert spec.loader is not None
        spec.loader.exec_module(self.runner)  # type: ignore[union-attr]

        self.tmpdir = tempfile.mkdtemp(prefix="test-apply-dir-")
        self.runner.CHANGES_DIR = self.tmpdir  # type: ignore[attr-defined]
        self.change = "layout-change"
        self.apply_dir = os.path.join(self.tmpdir, self.change, "2-build")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_apply_dir_constant(self):
        self.assertEqual(self.runner.APPLY_DIR, "2-build")
        self.assertIn(".context-chain.state", self.runner.APPLY_STATE_FILES)
        self.assertIn(".pipeline-state.json", self.runner.APPLY_STATE_FILES)

    def test_get_apply_dir_and_state_path(self):
        self.assertEqual(
            self.runner.get_apply_dir(self.change),
            os.path.join(self.tmpdir, self.change, "2-build"),
        )
        self.assertTrue(
            self.runner.get_state_path(self.change).endswith(
                "2-build/.pipeline-state.json"
            )
        )

    def test_gate_report_path_infers_from_subdir(self):
        os.makedirs(self.apply_dir, exist_ok=True)
        for n in (1, 2, 3):
            open(os.path.join(self.apply_dir, f"backend-{n}-gate-assessment.md"), "w").close()
        # Old-style file at change root must be ignored
        open(
            os.path.join(self.tmpdir, self.change, "gate-assessment-backend.md"), "w"
        ).close()

        result = self.runner.gate_report_path_for(self.change, "backend")
        self.assertTrue(
            result.endswith("2-build/backend-4-gate-assessment.md"), result
        )

    def test_track_latest_report_in_subdir(self):
        os.makedirs(self.apply_dir, exist_ok=True)
        open(os.path.join(self.apply_dir, "backend-2-verify.md"), "w").close()
        open(os.path.join(self.apply_dir, "backend-5-verify.md"), "w").close()
        result = self.runner.track_latest_report_path(self.change, "backend", "verify")
        self.assertTrue(result.endswith("2-build/backend-5-verify.md"), result)

    def test_migrate_legacy_state_files(self):
        change_root = os.path.join(self.tmpdir, self.change)
        os.makedirs(change_root)
        os.makedirs(self.apply_dir, exist_ok=True)
        # Legacy files at change root
        open(os.path.join(change_root, ".context-chain.state"), "w").write("k=v\n")
        # .pg-spec.yaml is no longer generated; legacy file should be cleaned up
        open(os.path.join(change_root, ".pg-spec.yaml"), "w").write("k: v\n")
        # target already exists → legacy should be removed
        open(os.path.join(self.apply_dir, ".pipeline-state.json"), "w").write("new\n")
        open(os.path.join(change_root, ".pipeline-state.json"), "w").write("old\n")

        moved = self.runner.migrate_legacy_state_files(self.change)

        self.assertTrue(os.path.isfile(os.path.join(self.apply_dir, ".context-chain.state")))
        self.assertTrue(os.path.isfile(os.path.join(self.apply_dir, ".pipeline-state.json")))
        self.assertFalse(os.path.isfile(os.path.join(change_root, ".context-chain.state")))
        self.assertFalse(os.path.isfile(os.path.join(change_root, ".pg-spec.yaml")))
        self.assertFalse(os.path.isfile(os.path.join(change_root, ".pipeline-state.json")))
        # .pg-spec.yaml is cleaned up (not moved), so only 2 state files
        self.assertEqual(len(moved), 2)

        # Idempotent
        moved2 = self.runner.migrate_legacy_state_files(self.change)
        self.assertEqual(moved2, [])


if __name__ == "__main__":
    unittest.main()
