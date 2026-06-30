#!/usr/bin/env python3
"""test_v2_branch_isolation.py — Integration test for v2 path branch isolation.

Verifies that the v2 path (state_v2.enabled=true) creates and uses the
feat/pg/<change> branch correctly, mirroring v1 behavior.

This test is the regression guard for the v2 bootstrap regression
(commit fix: v2 cmd_next_v2 补回 feat/pg/<change> 分支创建). Without
these fixes, the v2 path would commit all changes directly to master,
breaking the pg-verify-and-merge flow.

Scope:
- Mock-heavy integration test: uses real PipelineState + real git, but
  mocks the actual sub-agent dispatches (no LLM calls).
- Runs in <5s.

What it verifies:
1. Before any cmd_next_v2 call: HEAD is on master.
2. After first cmd_next_v2 call: HEAD is on feat/pg/<change>.
3. After a record completed + next: an auto-record commit lands on
   feat/pg/<change>, NOT on master.
4. The context-chain.md file is created in 2-build/.
5. The init_commit dict is attached to the first dispatch action.
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


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _git_branch(cwd):
    return _git(cwd, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def _git_log(cwd, *args):
    r = _git(cwd, "log", *args)
    return r.stdout.strip()


def _load_module(name, path):
    """Import a module fresh from path."""
    if name in sys.modules:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class _V2BranchIsolationBase(unittest.TestCase):
    """Common scaffolding for v2 branch isolation tests."""

    def setUp(self):
        # 1. Create a temp project with .pg/changes/<change>/ scaffold
        self.tmpdir = tempfile.mkdtemp(prefix="test-v2-branch-iso-")
        self.pg_spec = os.path.join(self.tmpdir, "pg-spec")
        self.changes_dir = os.path.join(self.pg_spec, "changes")
        self.change = "v2-branch-iso-test"
        self.change_dir = os.path.join(self.changes_dir, self.change)
        os.makedirs(self.change_dir)
        os.makedirs(os.path.join(self.change_dir, "2-build"))

        # 2. Minimal config.yaml
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

        # 3. Proposal artifacts
        for name in ("proposal.md", "design.md", "tasks.md"):
            with open(os.path.join(self.change_dir, name), "w") as f:
                f.write(f"# {name}\n")
        with open(os.path.join(self.change_dir, "tasks.md"), "w") as f:
            f.write(
                "# Tasks\n\n"
                "## 1. dummy-track:test - dummy test\n\n"
                "- [ ] 1.1 do something\n"
            )

        # 4. Initialize git repo on master
        _git(self.tmpdir, "init", "-q", "-b", "master")
        _git(self.tmpdir, "config", "user.email", "t@t")
        _git(self.tmpdir, "config", "user.name", "t")
        _git(self.tmpdir, "commit", "--allow-empty", "-q", "-m", "baseline")

        # 5. Load modules — bind runner to our temp project.
        # IMPORTANT: load pg_context_chain BEFORE pg_pipeline_runner so
        # the runner's `import pg_context_chain` resolves to our rebound
        # module (which has the test tempdir's PROJECT_ROOT/CHANGES_DIR).
        pgcc = _load_module("pg_context_chain",
                            os.path.join(SCRIPTS_DIR, "pg_context_chain.py"))
        setattr(pgcc, "PROJECT_ROOT", self.tmpdir)
        setattr(pgcc, "CHANGES_DIR", self.changes_dir)

        self.runner = _load_module("pg_pipeline_runner",
                                   os.path.join(SCRIPTS_DIR, "pg-pipeline-runner.py"))
        setattr(self.runner, "PROJECT_ROOT", self.tmpdir)
        setattr(self.runner, "CHANGES_DIR", self.changes_dir)
        # CONFIG_PATH is bound at import time; rebind it to the test tempdir
        # so load_config() finds the test config.yaml.
        setattr(self.runner, "CONFIG_PATH", os.path.join(self.pg_spec, "config.yaml"))
        # Also rebind runner's pg_context_chain reference (used by helpers).
        setattr(self.runner, "pg_context_chain", pgcc)
        # Force-load common so v2 can import it
        _load_module("pg_pipeline_common",
                     os.path.join(SCRIPTS_DIR, "pg_pipeline_common.py"))
        self.v2 = _load_module("pg_runner_v2",
                               os.path.join(SCRIPTS_DIR, "pg_runner_v2.py"))

    def tearDown(self):
        for name in ("pg_pipeline_runner", "pg_pipeline_common",
                     "pg_context_chain", "pg_runner_v2",
                     "pg_pipeline_state_v2"):
            if name in sys.modules:
                del sys.modules[name]
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestV2CreatesFeatureBranch(_V2BranchIsolationBase):
    """Verify pg_build_bootstrap is called and creates the feat branch."""

    def test_first_cmd_next_v2_creates_feat_branch(self):
        """First cmd_next_v2: HEAD moves from master → feat/pg/<change>."""
        # Sanity: starts on master
        self.assertEqual(_git_branch(self.tmpdir), "master")

        # Mock PipelineState to be empty (so cmd_next_v2 returns workflow_failed
        # which is fine — we only care about side effects up to that point)
        class FakePS:
            data = {"context": {}, "current_dispatch": None}
            def next_pending(self): return None
            def set_pipeline_order(self, order): pass

        with mock.patch.object(self.v2, "PipelineState", return_value=FakePS()), \
             mock.patch.object(self.v2, "_find_cwd_project_root",
                               return_value=self.tmpdir), \
             mock.patch.object(self.runner, "pg_build_bootstrap",
                               wraps=self.runner.pg_build_bootstrap) as mock_bs, \
             mock.patch.object(self.runner, "_validate_manifest",
                               return_value=(True, "")), \
             mock.patch.object(self.runner, "get_pipeline_order",
                               return_value=[]):
            self.v2.cmd_next_v2(self.change)

        # Bootstrap should have been called
        mock_bs.assert_called_once()

        # Branch should be feat/pg/<change>
        branch = _git_branch(self.tmpdir)
        self.assertEqual(branch, f"feat/pg/{self.change}",
                         f"expected feat/pg/{self.change}, got {branch}")


class TestV2ContextChainCreated(_V2BranchIsolationBase):
    """Verify context-chain.md is created in 2-build/."""

    def test_context_chain_md_created(self):
        """After first cmd_next_v2, .pg/changes/<change>/2-build/context-chain.md exists."""
        # Sanity: file does NOT exist yet
        cc_path = os.path.join(self.change_dir, "2-build", "context-chain.md")
        self.assertFalse(os.path.exists(cc_path))

        class FakePS:
            data = {"context": {}, "current_dispatch": None}
            def next_pending(self): return None
            def set_pipeline_order(self, order): pass

        with mock.patch.object(self.v2, "PipelineState", return_value=FakePS()), \
             mock.patch.object(self.v2, "_find_cwd_project_root",
                               return_value=self.tmpdir), \
             mock.patch.object(self.runner, "_ensure_context_chain",
                               wraps=self.runner._ensure_context_chain) as mock_ensure, \
             mock.patch.object(self.runner, "_validate_manifest",
                               return_value=(True, "")), \
             mock.patch.object(self.runner, "get_pipeline_order",
                               return_value=[]):
            self.v2.cmd_next_v2(self.change)

        # _ensure_context_chain should have been called
        mock_ensure.assert_called_once()

        # Now the file should exist
        self.assertTrue(os.path.exists(cc_path),
                        f"context-chain.md should be created at {cc_path}")


class TestV2InitCommitMounted(_V2BranchIsolationBase):
    """Verify init_commit is mounted on the first dispatch action."""

    def test_init_commit_attached_to_first_dispatch(self):
        """When the first dispatch happens, init_commit is in the action JSON."""
        from pg_pipeline_state_v2 import NextDispatch

        class FakeDispatch:
            def __init__(self):
                self.kind = "dispatch"
                self.track = "dummy-track"
                self.phase = "test"
                self.agent = "pg-build/test"
                self.cycle = 1
                self.is_resume = False

        class FakePS:
            def __init__(self):
                self.data = {"context": {}, "current_dispatch": None}
            def next_pending(self): return FakeDispatch()
            def set_pipeline_order(self, order): pass
            def record_dispatch_started(self, **kw): pass
            def commit(self): pass

        # Capture what dispatch_action is called with
        captured = {}

        def fake_dispatch_action(agent, item, sub, context, attempt, init_commit=None):
            captured["init_commit"] = init_commit
            return {"action": "dispatch", "agent": agent, "item": item,
                    "sub": sub, "init_commit": init_commit}

        with mock.patch.object(self.v2, "PipelineState", return_value=FakePS()), \
             mock.patch.object(self.v2, "_find_cwd_project_root",
                               return_value=self.tmpdir), \
             mock.patch.object(self.runner, "_validate_manifest",
                               return_value=(True, "")), \
             mock.patch.object(self.runner, "load_config", return_value={}), \
             mock.patch.object(self.runner, "get_pipeline_order",
                               return_value=["dummy-track"]), \
             mock.patch.object(self.runner, "dispatch_action",
                               side_effect=fake_dispatch_action), \
             mock.patch.object(self.runner, "pg_build_dispatch_context",
                               return_value=({"_change": self.change}, False)):
            result = self.v2.cmd_next_v2(self.change)

        # init_commit should be passed to dispatch_action
        self.assertIn("init_commit", captured)
        ic = captured["init_commit"]
        self.assertIsNotNone(ic, "init_commit should be set on first dispatch")
        self.assertEqual(ic.get("branch"), f"feat/pg/{self.change}")
        self.assertTrue(ic.get("committed", False),
                        f"init_commit.committed should be True, got {ic}")


class TestV2RecordCommitsOnFeatureBranch(_V2BranchIsolationBase):
    """Verify auto-record commits land on feat/pg/<change>, not master."""

    def test_record_commit_lands_on_feature_branch(self):
        """After a record call, the auto-record git commit is on feat/pg/<change>."""
        from pg_pipeline_state_v2 import NextDispatch

        class FakeDispatch:
            def __init__(self):
                self.kind = "dispatch"
                self.track = "dummy-track"
                self.phase = "test"
                self.agent = "pg-build/test"
                self.cycle = 1
                self.is_resume = False

        class FakePS:
            def __init__(self):
                self.data = {"context": {}, "current_dispatch": None}
            def next_pending(self): return FakeDispatch()
            def set_pipeline_order(self, order): pass
            def record_dispatch_started(self, **kw): pass
            def commit(self): pass
            def record_completed(self, *a, **kw): pass

        with mock.patch.object(self.v2, "PipelineState", return_value=FakePS()), \
             mock.patch.object(self.v2, "_find_cwd_project_root",
                               return_value=self.tmpdir), \
             mock.patch.object(self.runner, "_validate_manifest",
                               return_value=(True, "")), \
             mock.patch.object(self.runner, "load_config", return_value={}), \
             mock.patch.object(self.runner, "get_pipeline_order",
                               return_value=["dummy-track"]), \
             mock.patch.object(self.runner, "dispatch_action",
                               return_value={"action": "dispatch", "item": "dummy-track",
                                             "sub": "dev"}), \
             mock.patch.object(self.runner, "pg_build_dispatch_context",
                               return_value=({"_change": self.change}, False)):
            # Trigger the bootstrap + first dispatch (moves to feat branch)
            self.v2.cmd_next_v2(self.change)

        # Now HEAD should be on feat/pg/<change>
        self.assertEqual(_git_branch(self.tmpdir), f"feat/pg/{self.change}")

        # The bootstrap commit should be on top of feat/pg/<change>
        feat_log = _git_log(self.tmpdir, f"feat/pg/{self.change}", "--format=%s")
        self.assertIn("bootstrap", feat_log,
                      f"bootstrap commit should be on feat branch. log:\n{feat_log}")

        # Master should NOT have the bootstrap commit
        master_log = _git_log(self.tmpdir, "master", "--format=%s")
        self.assertNotIn("bootstrap", master_log,
                         f"master should not have bootstrap commit. log:\n{master_log}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
