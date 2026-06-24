#!/usr/bin/env python3
"""Integration test for the final-gate pass → archive no-orphan guarantee.

Reproduces the bug fixed in pg-pipeline-runner.py: final-gate pass path
previously called save_state AFTER archive, causing the runner to silently
recreate the source change dir and stage a second commit that re-introduced
the orphan `.pipeline-state.json` at the old path.

This test exercises the runner's final-gate pass branch (without going
through real agents) by:
  1. Staging a fake change dir with a tasks.md, .pipeline-state.json, etc.
  2. Initializing a temp git repo (so the runner's git rm + commit can run).
  3. Mocking the pipeline_mark / pg_context_chain.sub_end side-effects.
  4. Calling cmd_record(change, "pass") with current.item = "final-gate".
  5. Asserting:
     - Source change dir does NOT exist after pass.
     - Archive dir exists and contains .pipeline-state.json with
       completed=true and current=null.
     - Working tree has no uncommitted changes (no orphan re-added).
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
RUNNER_PY = os.path.join(SCRIPTS_DIR, "pg-pipeline-runner.py")
PG_ARCHIVE_PY = os.path.join(
    os.path.dirname(SCRIPTS_DIR),  # .opencode/skills/pg-build
    "..", "..", "pg-archive", "scripts", "pg-archive.py",
)


class TestFinalGateArchiveNoOrphan(unittest.TestCase):
    def setUp(self):
        # Isolated temp project root
        self.tmpdir = tempfile.mkdtemp(prefix="test-final-gate-archive-")
        self.pg_spec = os.path.join(self.tmpdir, "pg-spec")
        self.changes_dir = os.path.join(self.pg_spec, "changes")
        self.change = "no-orphan-change"
        self.change_dir = os.path.join(self.changes_dir, self.change)
        self.apply_dir = os.path.join(self.change_dir, "2-build")
        os.makedirs(self.apply_dir)

        # Minimal v3.0 config.yaml so runner can load it.
        # Note: final-gate is a runner-internal marker, NOT a stage — the
        # runner's get_pipeline_order synthesises it after the last stage.
        with open(os.path.join(self.pg_spec, "config.yaml"), "w") as f:
            f.write(
                "schema: spec-driven\n"
                "modules: {}\n"
                "tracks: {}\n"
                "stages: []\n"
            )

        # Tasks / proposal / design for completeness
        for name in ("proposal.md", "design.md"):
            with open(os.path.join(self.change_dir, name), "w") as f:
                f.write(f"# {name}\n")
        with open(os.path.join(self.change_dir, "tasks.md"), "w") as f:
            f.write("# Tasks\n")

        # Initialize a git repo at the project root so git rm/add/commit
        # inside runner don't fail with "not a git repository".
        subprocess.run(["git", "init", "-q", "-b", "master"], cwd=self.tmpdir, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=self.tmpdir, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=self.tmpdir, check=True)
        # Commit baseline so .pipeline-state.json at the source path is
        # tracked from the start (mimics real workflow).
        subprocess.run(["git", "add", "-A"], cwd=self.tmpdir, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=self.tmpdir, check=True)

        # Import runner module from the project root context
        # CRITICAL: SCRIPTS_DIR must be on sys.path so the runner's
        # `import pg_context_chain` works.
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)
        if "pg_pipeline_runner" in sys.modules:
            del sys.modules["pg_pipeline_runner"]
        spec = importlib.util.spec_from_file_location("pg_pipeline_runner", RUNNER_PY)
        assert spec is not None and spec.loader is not None
        self.runner = importlib.util.module_from_spec(spec)
        sys.modules["pg_pipeline_runner"] = self.runner
        spec.loader.exec_module(self.runner)
        # Force runner to use the isolated tmp project root
        setattr(self.runner, "PROJECT_ROOT", self.tmpdir)
        setattr(self.runner, "CHANGES_DIR", self.changes_dir)
        setattr(self.runner, "APPLY_STATE_FILES", (".context-chain.state", ".pipeline-state.json"))

    def tearDown(self):
        if "pg_pipeline_runner" in sys.modules:
            del sys.modules["pg_pipeline_runner"]
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _build_state(self):
        """Build a state dict as if runner is on final-gate pass."""
        return {
            "version": 1,
            "change": self.change,
            "failed": False,
            "current": {
                "item": "final-gate",
                "sub": "gate",
                "attempt": 1,
                "waiting": True,
            },
            "completed_items": ["backend", "frontend"],
        }

    def _seed_state_file(self):
        """Write the in-progress state to disk so cmd_record's load_state
        finds the current=final-gate item it needs."""
        state_path = os.path.join(self.apply_dir, ".pipeline-state.json")
        with open(state_path, "w") as f:
            json.dump(self._build_state(), f, indent=2)

    def test_final_gate_pass_archives_without_orphan(self):
        self._seed_state_file()
        state = self._build_state()

        # Patch side-effecting helpers in the runner module
        with mock.patch.object(self.runner, "pipeline_mark", return_value={}), \
             mock.patch.object(self.runner, "pg_context_chain") as mock_chain, \
             mock.patch.object(self.runner, "_auto_commit_on_record", return_value={
                 "attempted": True, "committed": False, "reason": "test stub",
             }):
            mock_chain.sub_end = mock.MagicMock()
            mock_chain.sub_start = mock.MagicMock()

            # Run the cmd_record path with status="pass" for final-gate.
            # cmd_record signature: (change, status, *rest)
            # Looking at the code, the relevant branch is in the
            # elif status == "pass" block — we drive it through the
            # public cmd_record entrypoint.
            result = self.runner.cmd_record(self.change, "pass", "", "all good")

        # 1. Final-gate pass returns action=done
        self.assertEqual(result.get("action"), "done")
        self.assertEqual(result.get("status"), "completed")

        # 2. Source change dir must NOT exist
        self.assertFalse(
            os.path.isdir(self.change_dir),
            f"BUG: source change dir still exists after archive: {self.change_dir}",
        )

        # 3. Archive dir exists and contains final state file
        archive_root = os.path.join(self.changes_dir, "archive")
        self.assertTrue(os.path.isdir(archive_root))
        archive_entries = os.listdir(archive_root)
        self.assertEqual(len(archive_entries), 1, f"Expected 1 archive, got {archive_entries}")
        archived_change = os.path.join(archive_root, archive_entries[0])
        archived_state = os.path.join(archived_change, "2-build", ".pipeline-state.json")
        self.assertTrue(
            os.path.isfile(archived_state),
            f"Archived state file missing: {archived_state}",
        )

        # 4. Archived state file contains the FINAL state
        # (completed=true, current=null) — this is the key invariant:
        # save_state must be called BEFORE archive, so the final state
        # is moved along with the rest of the dir.
        with open(archived_state) as f:
            final_state = json.load(f)
        self.assertTrue(final_state.get("completed"), f"completed missing in archived state: {final_state}")
        self.assertIsNone(
            final_state.get("current"),
            f"current should be null in archived state: {final_state}",
        )

        # 5. Working tree must be clean — no orphan re-added
        porcelain = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.tmpdir, capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(
            porcelain, "",
            f"BUG: working tree dirty after archive (orphan re-staged):\n{porcelain}",
        )

        # 6. Git log should contain exactly the archive commit and the init
        # commit — no second auto-record commit re-introducing the orphan.
        log = subprocess.run(
            ["git", "log", "--format=%s"],
            cwd=self.tmpdir, capture_output=True, text=True,
        ).stdout.strip().splitlines()
        auto_record_commits = [
            l for l in log
            if "auto-record final-gate" in l
        ]
        self.assertEqual(
            auto_record_commits, [],
            f"BUG: auto-record final-gate commit re-introduced orphan: {auto_record_commits}",
        )

    def test_save_state_runs_before_archive_in_final_gate_pass(self):
        """Direct order check: save_state must complete before _auto_archive.

        We patch both functions and assert call order.
        """
        self._seed_state_file()
        state = self._build_state()
        call_order = []

        def fake_save_state(s):
            call_order.append("save_state")
            return None

        def fake_auto_archive(c):
            call_order.append("auto_archive")
            # Mimic real archive: actually move dir so the runner's
            # subsequent calls don't blow up
            src = os.path.join(self.changes_dir, c)
            archive_root = os.path.join(self.changes_dir, "archive")
            os.makedirs(archive_root, exist_ok=True)
            target = os.path.join(archive_root, f"2026-01-01-{c}")
            shutil.move(src, target)
            return {"ok": True, "target_name": os.path.basename(target),
                    "src": f".pg/changes/{c}",
                    "target": f".pg/changes/archive/{os.path.basename(target)}"}

        def fake_git_commit(ar):
            call_order.append("git_commit_archive")
            return {"attempted": True, "committed": True, "branch": "master",
                    "sha": "deadbeef", "message": "archive change"}

        with mock.patch.object(self.runner, "save_state", side_effect=fake_save_state), \
             mock.patch.object(self.runner, "_auto_archive", side_effect=fake_auto_archive), \
             mock.patch.object(self.runner, "_git_commit_archive", side_effect=fake_git_commit), \
             mock.patch.object(self.runner, "pipeline_mark", return_value={}), \
             mock.patch.object(self.runner, "pg_context_chain") as mock_chain, \
             mock.patch.object(self.runner, "_auto_commit_on_record", return_value={
                 "attempted": True, "committed": False, "reason": "stub",
             }):
            mock_chain.sub_end = mock.MagicMock()

            self.runner.cmd_record(self.change, "pass", "", "ok")

        # Strict ordering: save_state must be before auto_archive.
        self.assertLess(
            call_order.index("save_state"),
            call_order.index("auto_archive"),
            f"BUG: save_state ran AFTER auto_archive. order={call_order}",
        )


if __name__ == "__main__":
    unittest.main()
