#!/usr/bin/env python3
"""Tests for the bootstrap init-commit in cmd_next.

Covers:
- `chore(<change>): bootstrap pg-build` is created exactly once on the
  first dispatch.
- The commit message format is fixed.
- The init commit lands on `feat/pg/<change>` (i.e. AFTER `_ensure_feature_branch`).
- Idempotency: a second `cmd_next` call does NOT create a duplicate commit.
- Failure is non-fatal: even when `_auto_commit_on_init` reports failure,
  `state["init_committed"]` is set so we never retry, and dispatch still
  proceeds.
- `init_commit` is attached to dispatch action on first call only; not on
  `dispatch_fix` / `dispatch_final_gate` / `execute_phase` / `done` /
  `workflow_failed`.
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


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _git_log_subjects(cwd):
    r = _git(cwd, "log", "--format=%s")
    return [line for line in r.stdout.strip().splitlines() if line]


def _git_head_sha(cwd):
    return _git(cwd, "rev-parse", "HEAD").stdout.strip()


def _git_branch(cwd):
    return _git(cwd, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def _git_status_porcelain(cwd):
    return _git(cwd, "status", "--porcelain").stdout.strip()


def _load_runner():
    """Import the runner module fresh so module-level state is clean per call."""
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    if "pg_pipeline_runner" in sys.modules:
        del sys.modules["pg_pipeline_runner"]
    spec = importlib.util.spec_from_file_location("pg_pipeline_runner", RUNNER_PY)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    sys.modules["pg_pipeline_runner"] = runner
    spec.loader.exec_module(runner)
    return runner


class _InitCommitTestBase(unittest.TestCase):
    """Common scaffolding for init-commit tests.

    Each test starts on `master` with proposal artifacts committed; the
    runner is then expected to (a) switch to feat/pg/<change>, (b) write a
    clean `.pipeline-state.json`, and (c) create the bootstrap commit.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test-init-commit-")
        self.pg_spec = os.path.join(self.tmpdir, "pg-spec")
        self.changes_dir = os.path.join(self.pg_spec, "changes")
        self.change = "init-commit-change"
        self.change_dir = os.path.join(self.changes_dir, self.change)
        self.apply_dir = os.path.join(self.change_dir, "2-build")
        os.makedirs(self.change_dir)

        # Minimal v3.0 config so load_config works
        with open(os.path.join(self.pg_spec, "config.yaml"), "w") as f:
            f.write(
                "schema: spec-driven\n"
                "modules:\n"
                "  dummy-mod:\n"
                "    root: /tmp\n"
                "    language: python\n"
                "    test:\n"
                "      unit: 'true'\n"
                "tracks:\n"
                "  dummy-track:\n"
                "    modules: [dummy-mod]\n"
                "    max_fix_retries: 5\n"
                "    fix_routing: source\n"
                "    review_level: none\n"
                "    label: dummy\n"
                "stages:\n"
                "  - name: dev-isolated\n"
                "    tracks: [dummy-track]\n"
                "    test_key: unit\n"
                "    environment:\n"
                "      required: false\n"
                "    gate: all_pass\n"
            )

        # Proposal artifacts at change root — these are what init commit
        # should pick up.
        for name in ("proposal.md", "design.md", "tasks.md"):
            with open(os.path.join(self.change_dir, name), "w") as f:
                f.write(f"# {name}\n")

        # tasks.md with one track section so detect() finds it
        with open(os.path.join(self.change_dir, "tasks.md"), "w") as f:
            f.write(
                "# Tasks\n\n"
                "## 1. dummy-track:test - dummy test\n\n"
                "- [ ] 1.1 do something\n\n"
            )

        # git repo — do NOT pre-commit the proposal files; they should
        # remain dirty so the bootstrap init commit picks them up.
        _git(self.tmpdir, "init", "-q", "-b", "master")
        _git(self.tmpdir, "config", "user.email", "t@t")
        _git(self.tmpdir, "config", "user.name", "t")
        # Commit an empty baseline just so HEAD exists (required for
        # `git checkout -b feat/pg/<change>`).
        _git(self.tmpdir, "commit", "--allow-empty", "-q", "-m", "empty baseline")

        self.runner = _load_runner()
        # Bind runner to our isolated project root
        setattr(self.runner, "PROJECT_ROOT", self.tmpdir)
        setattr(self.runner, "CHANGES_DIR", self.changes_dir)

    def tearDown(self):
        if "pg_pipeline_runner" in sys.modules:
            del sys.modules["pg_pipeline_runner"]
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestInitCommitFiresOnce(_InitCommitTestBase):
    def test_first_dispatch_creates_bootstrap_commit_on_feature_branch(self):
        """First cmd_next → bootstrap commit lands on feat/pg/<change>
        with the fixed message format."""
        # Track pipeline-detect + sub_start side effects so we don't actually
        # call pg-pipeline-state.py from inside the runner.
        with (
            mock.patch.object(
                self.runner,
                "pipeline_detect",
                return_value={
                    "item": "dummy-track",
                    "type": "track",
                    "subPhase": "test",
                },
            ),
            mock.patch.object(self.runner, "pg_context_chain") as mock_chain,
        ):
            mock_chain.sub_start = mock.MagicMock()
            mock_chain.rollback_get = mock.MagicMock(return_value={"found": False})

            result = self.runner.cmd_next(self.change)

        # 1. Returned action is a dispatch
        self.assertEqual(result["action"], "dispatch")
        self.assertEqual(result["item"], "dummy-track")
        self.assertEqual(result["sub"], "test")

        # 2. init_commit field present on first dispatch
        self.assertIn("init_commit", result)
        self.assertTrue(result["init_commit"]["committed"], result["init_commit"])
        self.assertEqual(
            result["init_commit"]["message"],
            f"chore({self.change}): bootstrap pg-build",
        )

        # 3. Branch is feat/pg/<change>
        branch = _git_branch(self.tmpdir)
        self.assertEqual(branch, f"feat/pg/{self.change}")

        # 4. git log on feature branch has the bootstrap commit on top
        subjects = _git_log_subjects(self.tmpdir)
        self.assertEqual(subjects[0], f"chore({self.change}): bootstrap pg-build")

        # 5. .pipeline-state.json was written with init_committed=True
        with open(os.path.join(self.apply_dir, ".pipeline-state.json")) as f:
            state = json.load(f)
        self.assertTrue(state.get("init_committed"), state)

    def test_second_cmd_next_does_not_recommit(self):
        """Idempotency: a second cmd_next must NOT create another
        bootstrap commit."""
        # First call: real init commit
        with (
            mock.patch.object(
                self.runner,
                "pipeline_detect",
                return_value={
                    "item": "dummy-track",
                    "type": "track",
                    "subPhase": "test",
                },
            ),
            mock.patch.object(self.runner, "pg_context_chain") as mock_chain,
        ):
            mock_chain.sub_start = mock.MagicMock()
            mock_chain.rollback_get = mock.MagicMock(return_value={"found": False})
            self.runner.cmd_next(self.change)
        first_sha = _git_head_sha(self.tmpdir)
        first_subjects = _git_log_subjects(self.tmpdir)
        first_init_count = sum(1 for s in first_subjects if "bootstrap pg-build" in s)
        self.assertEqual(first_init_count, 1)

        # Second call: runner should detect init_committed=True and skip
        # the commit. Use a waiting state so we hit _resume_waiting (the
        # path most likely to leak).
        with mock.patch.object(self.runner, "pg_context_chain") as mock_chain:
            mock_chain.rollback_get = mock.MagicMock(return_value={"found": False})
            result2 = self.runner.cmd_next(self.change)

        # 6. Second dispatch has NO init_commit field
        self.assertNotIn("init_commit", result2)

        # 7. HEAD did not move, no new bootstrap commit created
        second_sha = _git_head_sha(self.tmpdir)
        self.assertEqual(first_sha, second_sha)
        second_subjects = _git_log_subjects(self.tmpdir)
        second_init_count = sum(1 for s in second_subjects if "bootstrap pg-build" in s)
        self.assertEqual(second_init_count, 1)


class TestInitCommitFailureNonFatal(_InitCommitTestBase):
    def test_init_commit_failure_does_not_block_dispatch(self):
        """When _auto_commit_on_init reports failure, dispatch still
        proceeds and the init_committed marker is set so we do not retry."""
        with (
            mock.patch.object(
                self.runner,
                "pipeline_detect",
                return_value={
                    "item": "dummy-track",
                    "type": "track",
                    "subPhase": "test",
                },
            ),
            mock.patch.object(self.runner, "pg_context_chain") as mock_chain,
            mock.patch.object(
                self.runner,
                "_auto_commit_on_init",
                return_value={
                    "attempted": True,
                    "committed": False,
                    "branch": "master",
                    "reason": "simulated git failure",
                },
            ),
        ):
            mock_chain.sub_start = mock.MagicMock()
            mock_chain.rollback_get = mock.MagicMock(return_value={"found": False})
            result = self.runner.cmd_next(self.change)

        # 1. dispatch still returned (not workflow_failed)
        self.assertEqual(result["action"], "dispatch")

        # 2. init_commit surfaces the failure to LLM
        self.assertIn("init_commit", result)
        self.assertFalse(result["init_commit"]["committed"])
        self.assertEqual(result["init_commit"]["reason"], "simulated git failure")

        # 3. State file still has init_committed=True (no retry next time)
        with open(os.path.join(self.apply_dir, ".pipeline-state.json")) as f:
            state = json.load(f)
        self.assertTrue(state.get("init_committed"), state)

        # 4. No bootstrap commit on the branch (the failure was real)
        subjects = _git_log_subjects(self.tmpdir)
        bootstrap_subjects = [s for s in subjects if "bootstrap pg-build" in s]
        self.assertEqual(bootstrap_subjects, [])


class TestInitCommitOrdering(_InitCommitTestBase):
    def test_init_commit_runs_after_ensure_feature_branch(self):
        """Init commit MUST happen after _ensure_feature_branch so the
        commit lands on feat/pg/<change> rather than master."""
        call_order = []

        real_ensure_feature_branch = self.runner._ensure_feature_branch
        real_init = self.runner._auto_commit_on_init

        def tracking_ensure(change):
            call_order.append("ensure_feature_branch")
            return real_ensure_feature_branch(change)

        def tracking_init(change):
            call_order.append("auto_commit_on_init")
            return real_init(change)

        with (
            mock.patch.object(
                self.runner,
                "pipeline_detect",
                return_value={
                    "item": "dummy-track",
                    "type": "track",
                    "subPhase": "test",
                },
            ),
            mock.patch.object(self.runner, "pg_context_chain") as mock_chain,
            mock.patch.object(
                self.runner,
                "_ensure_feature_branch",
                side_effect=tracking_ensure,
            ),
            mock.patch.object(
                self.runner,
                "_auto_commit_on_init",
                side_effect=tracking_init,
            ),
        ):
            mock_chain.sub_start = mock.MagicMock()
            mock_chain.rollback_get = mock.MagicMock(return_value={"found": False})
            self.runner.cmd_next(self.change)

        # ensure_feature_branch must precede auto_commit_on_init
        self.assertLess(
            call_order.index("ensure_feature_branch"),
            call_order.index("auto_commit_on_init"),
            f"BUG: order={call_order}",
        )

        # And the commit really did land on the feature branch, not master.
        # `git log feat/pg/<change>` should include the bootstrap commit
        # at HEAD.
        r = _git(self.tmpdir, "log", f"feat/pg/{self.change}", "--format=%s")
        subjects = [s for s in r.stdout.strip().splitlines() if s]
        self.assertEqual(subjects[0], f"chore({self.change}): bootstrap pg-build")


class TestMaybeBootstrapHelper(_InitCommitTestBase):
    """Direct unit tests for the helper, no full cmd_next invocation."""

    def test_returns_none_when_already_committed(self):
        """If state.init_committed=True, helper returns None and does
        not invoke the commit function."""
        state = {"version": 1, "change": self.change, "init_committed": True}
        with mock.patch.object(self.runner, "_auto_commit_on_init") as mock_init:
            result = self.runner._maybe_bootstrap_init_commit(self.change, state)
        self.assertIsNone(result)
        mock_init.assert_not_called()

    def test_runs_commit_and_mutates_state_on_first_call(self):
        """Helper mutates the passed-in state dict to set
        init_committed=True so the caller's later save_state persists it.

        Helper MUST persist state to disk BEFORE running the bootstrap
        commit, otherwise `git add -A` + `git commit` inside
        `_auto_commit_on_init` lands the commit before `.pipeline-state.json`
        is staged on disk — so the bootstrap commit silently excludes the
        freshly-created state file. Regression seen on
        `instance-detail-host-versions` (commit 6c7eb87a contained only
        context-chain.md, not .pipeline-state.json).
        """
        state = {"version": 1, "change": self.change, "failed": False}

        with mock.patch.object(
            self.runner,
            "_auto_commit_on_init",
            return_value={
                "attempted": True,
                "committed": True,
                "branch": "feat/x",
                "sha": "deadbeef",
                "message": "x",
                "reason": None,
            },
        ) as mock_init:
            result = self.runner._maybe_bootstrap_init_commit(self.change, state)

        self.assertEqual(result["committed"], True)
        self.assertEqual(result["sha"], "deadbeef")
        mock_init.assert_called_once_with(self.change)

        # State was mutated in place
        self.assertTrue(state.get("init_committed"))

        # Helper DID write state to disk BEFORE the init commit, so
        # `git add -A` includes `.pipeline-state.json` in the bootstrap
        # commit. (Previous "caller-only" contract caused regression where
        # the bootstrap commit missed state.json — see fix description.)
        state_file = os.path.join(self.apply_dir, ".pipeline-state.json")
        self.assertTrue(
            os.path.isfile(state_file),
            f"BUG: helper did NOT persist state before init commit; "
            f"`git add -A` will miss .pipeline-state.json: {state_file}",
        )
        with open(state_file) as f:
            persisted = json.load(f)
        self.assertTrue(
            persisted.get("init_committed"),
            f"persisted state missing init_committed marker: {persisted}",
        )

    def test_v2_context_dict_without_change_key_does_not_crash(self):
        """Bug B regression: when state is a v2 context dict (returned by
        _normalize_state_for_bootstrap) without a top-level 'change' key,
        save_state(state) must not be called (would raise KeyError).

        The helper should mutate the dict in-place and let the caller
        (_persist_state_mutation) persist later. No state file should
        be written directly by save_state.
        """
        # Simulate v2 context dict: no "change" key, no "version" key.
        state = {"init_committed": False, "pipeline_order": ["dev.prepare_env"]}

        with mock.patch.object(
            self.runner,
            "_auto_commit_on_init",
            return_value={
                "attempted": True,
                "committed": True,
                "branch": "feat/pg/bug-b",
                "sha": "cafebabe",
                "message": "test",
                "reason": None,
            },
        ) as mock_init:
            # Should NOT raise (save_state is guarded by "change" in state check)
            result = self.runner._maybe_bootstrap_init_commit(self.change, state)

        self.assertEqual(result["committed"], True)
        self.assertEqual(result["sha"], "cafebabe")
        mock_init.assert_called_once_with(self.change)

        # State mutated in place even without "change" key
        self.assertTrue(state.get("init_committed"))

        # save_state must NOT have written the context dict as the full
        # state.json (because that would corrupt the v2 state).
        state_file = os.path.join(self.apply_dir, ".pipeline-state.json")
        if os.path.isfile(state_file):
            with open(state_file) as f:
                persisted = json.load(f)
            # The persisted file should still be the v1-style state
            # (unchanged by this test — we only verified no crash).
            self.assertIn("change", persisted,
                          "Bug B save_state wrote context dict as state")


class TestInitCommitFieldMounting(_InitCommitTestBase):
    """Verify init_commit field is mounted on dispatch only, not on the
    other action types."""

    def test_init_commit_absent_on_dispatch_fix(self):
        """dispatch_fix is a sub-action during verify-fix cycle; not the
        first-dispatch path, so init_commit must NOT be present."""
        ctx = {"prompt_injection": {"prepend": "", "append": "", "rules_applied": []}}
        result = self.runner.dispatch_fix_action("dummy-track", 1, ctx)
        self.assertNotIn("init_commit", result)

    def test_init_commit_absent_on_dispatch_final_gate(self):
        """The final-gate entry path goes through _enter_final_gate,
        which returns dispatch_final_gate without init_commit."""
        state = {"version": 1, "change": self.change}
        config = self.runner.load_config()
        result = self.runner._enter_final_gate(config, self.change, state)
        self.assertNotIn("init_commit", result)

    def test_init_commit_absent_on_done_and_workflow_failed(self):
        """Terminal states must not have init_commit field."""
        # workflow_failed short-circuit at top of cmd_next
        state_path = self.runner.get_state_path(self.change)
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w") as f:
            json.dump(
                {"version": 1, "change": self.change, "completed": True},
                f,
            )
        result_done = self.runner.cmd_next(self.change)
        self.assertNotIn("init_commit", result_done)

        # workflow_failed
        with open(state_path, "w") as f:
            json.dump(
                {
                    "version": 1,
                    "change": self.change,
                    "failed": True,
                    "fail_reason": "x",
                },
                f,
            )
        result_failed = self.runner.cmd_next(self.change)
        self.assertNotIn("init_commit", result_failed)


if __name__ == "__main__":
    unittest.main()
