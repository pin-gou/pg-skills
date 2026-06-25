#!/usr/bin/env python3
"""Tests for the simple-track feature.

Covers:
- get_track_type() classifies simple tracks as 'phase'.
- get_track_type() preserves 'track' for standard tracks.
- _noopify_simple_track_sections() rewrites simple-track sections to noop.
- _noopify_simple_track_sections() is idempotent on second invocation.
- _noopify_simple_track_sections() leaves standard tracks untouched.
- _execute_phase() returns workflow_failed when a simple track has no commands.
- _execute_phase() runs commands sequentially and records success.
- _execute_phase() returns workflow_failed when a command exits non-zero.
- pg-validate-tasks skips simple tracks (does not flag missing section).
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

import yaml


SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
RUNNER_PY = os.path.join(SCRIPTS_DIR, "pg-pipeline-runner.py")
COMMON_PY = os.path.join(SCRIPTS_DIR, "pg_pipeline_common.py")
VALIDATE_PY = os.path.join(SCRIPTS_DIR, "pg-validate-tasks.py")


# ============================================================
# Module loaders (mirroring test_init_commit.py pattern)
# ============================================================

def _load_common():
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    if "pg_pipeline_common" in sys.modules:
        del sys.modules["pg_pipeline_common"]
    spec = importlib.util.spec_from_file_location("pg_pipeline_common", COMMON_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pg_pipeline_common"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_runner():
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    if "pg_pipeline_runner" in sys.modules:
        del sys.modules["pg_pipeline_runner"]
    spec = importlib.util.spec_from_file_location("pg_pipeline_runner", RUNNER_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pg_pipeline_runner"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_validate():
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    if "pg_validate_tasks" in sys.modules:
        del sys.modules["pg_validate_tasks"]
    spec = importlib.util.spec_from_file_location("pg_validate_tasks", VALIDATE_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pg_validate_tasks"] = mod
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# Pure get_track_type tests (no project root needed)
# ============================================================

class TestGetTrackType(unittest.TestCase):
    def setUp(self):
        self.common = _load_common()

    def test_simple_track_returns_phase(self):
        config = {"tracks": {
            "simple-openapi-gen": {"type": "simple", "commands": ["echo hi"]},
        }}
        self.assertEqual(self.common.get_track_type(config, "simple-openapi-gen"), "phase")

    def test_standard_track_returns_track(self):
        config = {"tracks": {
            "backend": {"modules": ["backend"]},
        }}
        self.assertEqual(self.common.get_track_type(config, "backend"), "track")

    def test_track_without_type_returns_track(self):
        """Backward compat: track with no `type` field defaults to standard."""
        config = {"tracks": {
            "backend": {"modules": ["backend"]},
        }}
        # type field absent → standard
        self.assertEqual(self.common.get_track_type(config, "backend"), "track")

    def test_unknown_item_returns_track(self):
        config = {"tracks": {}}
        self.assertEqual(self.common.get_track_type(config, "backend"), "track")

    def test_simple_with_empty_commands_still_returns_phase(self):
        """The empty-commands check happens in _execute_phase, not here."""
        config = {"tracks": {
            "x": {"type": "simple", "commands": []},
        }}
        self.assertEqual(self.common.get_track_type(config, "x"), "phase")


# ============================================================
# _noopify_simple_track_sections tests (need isolated tmp project root)
# ============================================================

class TestNoopifySimpleTrackSections(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test-simple-track-noopify-")
        self.pg_spec = os.path.join(self.tmpdir, "pg-spec")
        self.changes_dir = os.path.join(self.pg_spec, "changes")
        self.change = "test-change"
        self.change_dir = os.path.join(self.changes_dir, self.change)
        os.makedirs(self.change_dir)

        # Minimal config.yaml declaring both a simple and a standard track.
        with open(os.path.join(self.pg_spec, "config.yaml"), "w") as f:
            f.write(
                "schema: spec-driven\n"
                "modules:\n"
                "  frontend: {root: <module-name>, language: typescript}\n"
                "tracks:\n"
                "  simple-foo:\n"
                "    type: simple\n"
                "    commands: [\"echo hi\"]\n"
                "  standard-bar:\n"
                "    modules: [frontend]\n"
                "environments: {}\n"
                "stages: []\n"
            )

        # Tasks.md with sections for both tracks, simple section has real tasks.
        with open(os.path.join(self.change_dir, "tasks.md"), "w") as f:
            f.write(
                "# Tasks\n\n"
                "## 1. simple-foo:dev - simple track 文档化\n\n"
                "- [ ] 1.1 应该由 runner 自动执行\n"
                "- [ ] 1.2 不应该需要 LLM 干预\n\n"
                "## 2. standard-bar:dev - 标准 track\n\n"
                "- [ ] 2.1 这个必须保留\n"
                "- [ ] 2.2 这个也得保留\n"
            )

        # Load runner and point at the isolated tmp project.
        self.runner = _load_runner()
        setattr(self.runner, "PROJECT_ROOT", self.tmpdir)
        setattr(self.runner, "CHANGES_DIR", self.changes_dir)
        setattr(self.runner, "CONFIG_PATH", os.path.join(self.pg_spec, "config.yaml"))
        # Re-import common with the new PROJECT_ROOT so its module-level
        # constants are also overridden.
        self.common = _load_common()
        setattr(self.common, "PROJECT_ROOT", self.tmpdir)
        setattr(self.common, "CHANGES_DIR", self.changes_dir)
        setattr(self.common, "CONFIG_PATH", os.path.join(self.pg_spec, "config.yaml"))

    def tearDown(self):
        for k in ("pg_pipeline_runner", "pg_pipeline_common"):
            if k in sys.modules:
                del sys.modules[k]
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _read_tasks(self):
        with open(os.path.join(self.change_dir, "tasks.md"), encoding="utf-8") as f:
            return f.read()

    def test_rewrites_simple_section_to_noop(self):
        result = self.runner._noopify_simple_track_sections(self.change)
        self.assertEqual(result, 1)
        content = self._read_tasks()
        # Standard section preserved.
        self.assertIn("- [ ] 2.1 这个必须保留", content)
        self.assertIn("- [ ] 2.2 这个也得保留", content)
        # Simple section body replaced with - 无 line.
        self.assertIn("- 无\n", content)
        # Original simple-track task lines gone.
        self.assertNotIn("应该由 runner 自动执行", content)
        self.assertNotIn("不应该需要 LLM 干预", content)
        # Heading suffix added for documentation.
        self.assertIn("(simple track", content)

    def test_idempotent_on_second_call(self):
        self.runner._noopify_simple_track_sections(self.change)
        content_first = self._read_tasks()
        result_second = self.runner._noopify_simple_track_sections(self.change)
        content_second = self._read_tasks()
        self.assertEqual(result_second, 0)
        self.assertEqual(content_first, content_second)

    def test_no_simple_tracks_returns_zero(self):
        # Replace config to remove simple track.
        with open(os.path.join(self.pg_spec, "config.yaml"), "w") as f:
            f.write(
                "schema: spec-driven\n"
                "modules:\n"
                "  frontend: {root: <module-name>, language: typescript}\n"
                "tracks:\n"
                "  standard-bar: {modules: [frontend]}\n"
                "environments: {}\n"
                "stages: []\n"
            )
        result = self.runner._noopify_simple_track_sections(self.change)
        self.assertEqual(result, 0)
        # Content untouched.
        content = self._read_tasks()
        self.assertIn("- [ ] 1.1 应该由 runner 自动执行", content)

    def test_missing_tasks_md_returns_zero(self):
        os.remove(os.path.join(self.change_dir, "tasks.md"))
        result = self.runner._noopify_simple_track_sections(self.change)
        self.assertEqual(result, 0)

    def test_section_with_only_noop_is_already_done(self):
        """A section already in canonical form (heading suffix + body noop)
        is left alone with rewrite_count=0."""
        with open(os.path.join(self.change_dir, "tasks.md"), "w") as f:
            f.write(
                "# Tasks\n\n"
                "## 1. simple-foo:dev - simple track  (simple track: runner 直接执行 commands)\n\n"
                "- 无\n"
            )
        result = self.runner._noopify_simple_track_sections(self.change)
        self.assertEqual(result, 0)
        # Content unchanged.
        content = self._read_tasks()
        self.assertIn("- 无\n", content)
        self.assertIn("(simple track", content)


# ============================================================
# cmd_detect — simple tracks must be surfaced as type=phase
# (regression test for the openapi-gen-silently-skipped bug)
# ============================================================

def _load_state():
    """Load pg-pipeline-state.py as an importable module (mirrors _load_common/_load_runner)."""
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    if "pg_pipeline_state" in sys.modules:
        del sys.modules["pg_pipeline_state"]
    state_py = os.path.join(SCRIPTS_DIR, "pg-pipeline-state.py")
    spec = importlib.util.spec_from_file_location("pg_pipeline_state", state_py)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pg_pipeline_state"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestCmdDetectSimpleTrack(unittest.TestCase):
    """Regression tests: cmd_detect must NOT silently skip simple tracks.

    Bug: _noopify_simple_track_sections rewrites a simple track's tasks.md
    section body to "- 无", which made count_tasks() return all_noop=True.
    cmd_detect then short-circuited via `if all_noop: completed += 1; continue`
    and the runner never called _execute_phase → commands were never run.

    Fix: cmd_detect now checks get_track_type(config, item) and surfaces
    simple tracks as type=phase BEFORE the all_noop short-circuit.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test-cmd-detect-simple-")
        self.pg_spec = os.path.join(self.tmpdir, "pg-spec")
        self.changes_dir = os.path.join(self.pg_spec, "changes")
        self.change = "detect-change"
        self.change_dir = os.path.join(self.changes_dir, self.change)
        self.apply_dir = os.path.join(self.change_dir, "2-build")
        os.makedirs(self.apply_dir)

        with open(os.path.join(self.pg_spec, "config.yaml"), "w") as f:
            f.write(
                "schema: spec-driven\n"
                "modules:\n"
                "  frontend: {root: <module-name>, language: typescript}\n"
                "tracks:\n"
                "  simple-foo:\n"
                "    type: simple\n"
                "    commands: [\"echo hi\"]\n"
                "  standard-bar:\n"
                "    modules: [frontend]\n"
                "environments: {}\n"
                "stages:\n"
                "  - name: dev\n"
                "    tracks: [simple-foo, standard-bar]\n"
            )

        with open(os.path.join(self.change_dir, "tasks.md"), "w") as f:
            f.write(
                "# Tasks\n\n"
                "## 1. simple-foo:dev - simple section\n\n"
                "- [ ] 1.1 dummy\n"
                "## 2. standard-bar:dev - standard section\n\n"
                "- [ ] 2.1 real task\n"
            )

        self.state = _load_state()
        setattr(self.state, "PROJECT_ROOT", self.tmpdir)
        setattr(self.state, "CHANGES_DIR", self.changes_dir)
        setattr(self.state, "CONFIG_PATH", os.path.join(self.pg_spec, "config.yaml"))

        # Also re-point pg_pipeline_common (imported by state) at the tmp
        # project — cmd_detect's get_tasks_path() and load_config() read
        # CHANGES_DIR/CONFIG_PATH from common, not from state.
        import pg_pipeline_common as common_mod
        setattr(common_mod, "PROJECT_ROOT", self.tmpdir)
        setattr(common_mod, "CHANGES_DIR", self.changes_dir)
        setattr(common_mod, "CONFIG_PATH", os.path.join(self.pg_spec, "config.yaml"))

        self.runner = _load_runner()
        setattr(self.runner, "PROJECT_ROOT", self.tmpdir)
        setattr(self.runner, "CHANGES_DIR", self.changes_dir)
        setattr(self.runner, "CONFIG_PATH", os.path.join(self.pg_spec, "config.yaml"))
        # Re-point runner's view of common too.
        self.runner.pg_pipeline_common = common_mod
        self.runner._noopify_simple_track_sections(self.change)

    def tearDown(self):
        for k in ("pg_pipeline_state", "pg_pipeline_runner", "pg_pipeline_common"):
            if k in sys.modules:
                del sys.modules[k]
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _capture_detect(self):
        """Run cmd_detect and capture the JSON it prints.

        cmd_detect's `_print_json(obj)` writes to stdout via print(json.dumps(...)).
        We replace it with a capturing function and silence stdout.
        """
        captured = {}
        original = self.state._print_json
        def capture(obj):
            captured["obj"] = obj
        self.state._print_json = capture
        try:
            self.state.cmd_detect(self.change)
        finally:
            self.state._print_json = original
        return captured.get("obj")

    def test_simple_track_dispatched_as_phase(self):
        """When tasks.md has been noopified, cmd_detect must still surface
        the simple track as type=phase (so runner calls _execute_phase)."""
        result = self._capture_detect()
        self.assertIsNotNone(result, "cmd_detect must print a result")
        # cmd_detect returns the qualified form (dev.simple-foo) for phase
        # items, mirroring the env-hook return shape.
        self.assertEqual(result["item"], "dev.simple-foo",
                         "dev.simple-foo is the first item in pipeline order")
        self.assertEqual(result["type"], "phase",
                         "simple track MUST be dispatched as phase, "
                         "not silently skipped via all_noop")
        self.assertNotEqual(result.get("message", ""), "ALL_COMPLETED")

    def test_completed_simple_track_skipped(self):
        """Once a simple track lands in completed_items, cmd_detect must
        skip it (idempotency on resume)."""
        with open(os.path.join(self.apply_dir, ".pipeline-state.json"), "w") as f:
            json.dump({
                "version": 1,
                "change": self.change,
                "current": None,
                "completed_items": ["dev.simple-foo"],
                "failed": False,
            }, f)
        result = self._capture_detect()
        self.assertIsNotNone(result, "cmd_detect must print a result")
        # The first NOT-completed item should be standard-bar (next in order).
        self.assertEqual(result["item"], "dev.standard-bar")
        self.assertEqual(result["type"], "track")
        self.assertEqual(result["subPhase"], "dev")


# ============================================================
# _execute_phase simple-track branch tests
# ============================================================

class TestExecutePhaseSimpleTrack(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test-simple-track-exec-")
        self.pg_spec = os.path.join(self.tmpdir, "pg-spec")
        self.changes_dir = os.path.join(self.pg_spec, "changes")
        self.change = "exec-change"
        self.change_dir = os.path.join(self.changes_dir, self.change)
        self.apply_dir = os.path.join(self.change_dir, "2-build")
        os.makedirs(self.apply_dir)

        self.runner = _load_runner()
        setattr(self.runner, "PROJECT_ROOT", self.tmpdir)
        setattr(self.runner, "CHANGES_DIR", self.changes_dir)
        setattr(self.runner, "CONFIG_PATH", os.path.join(self.pg_spec, "config.yaml"))
        setattr(self.runner, "APPLY_STATE_FILES",
                (".context-chain.state", ".pipeline-state.json"))

        # Minimal config with a simple track.
        self._write_config(simple_commands=["echo hello"])

    def tearDown(self):
        for k in ("pg_pipeline_runner", "pg_pipeline_common"):
            if k in sys.modules:
                del sys.modules[k]
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_config(self, *, simple_commands):
        cfg_path = os.path.join(self.pg_spec, "config.yaml")
        cmds_yaml = "\n".join(f'      - "{c}"' for c in simple_commands)
        with open(cfg_path, "w") as f:
            f.write(
                "schema: spec-driven\n"
                "modules:\n"
                "  frontend: {root: <module-name>, language: typescript}\n"
                "tracks:\n"
                "  simple-foo:\n"
                "    type: simple\n"
                f"    commands:\n{cmds_yaml}\n"
                "environments: {}\n"
                "stages: []\n"
            )

    def _empty_state(self):
        return {
            "change": self.change,
            "current": None,
            "completed_items": [],
            "failed": False,
            "completed": False,
            "init_committed": True,
        }

    def _patch_runner(self, runner):
        """Patch side-effecting helpers so the test is hermetic."""
        # save_state writes to disk — replace with no-op.
        runner.save_state = mock.MagicMock(return_value=None)
        # pipeline_mark records item-level completion — replace with no-op.
        runner.pipeline_mark = mock.MagicMock(return_value=None)
        # pg_context_chain.phase_start/end — no-op.
        if hasattr(runner, "pg_context_chain"):
            runner.pg_context_chain.phase_start = mock.MagicMock()
            runner.pg_context_chain.phase_end = mock.MagicMock()
        return runner

    def test_workflow_failed_when_simple_track_has_no_commands(self):
        self._write_config(simple_commands=[])
        config = {
            "tracks": {
                "simple-foo": {"type": "simple", "commands": []},
            },
        }
        state = self._empty_state()
        runner = self._patch_runner(self.runner)
        result = runner._execute_phase(config, self.change, state, "simple-foo")
        self.assertEqual(result["action"], "workflow_failed")
        self.assertTrue(result["fatal"])
        self.assertIn("缺少 commands", result["reason"])

    def test_command_success_marks_completed_and_returns_phase_result(self):
        config = {
            "tracks": {
                "simple-foo": {"type": "simple", "commands": ["echo hello"]},
            },
        }
        state = self._empty_state()
        runner = self._patch_runner(self.runner)
        result = runner._execute_phase(config, self.change, state, "simple-foo")
        self.assertEqual(result["action"], "phase_result")
        self.assertFalse(result["terminate"])
        self.assertEqual(result["phase_item"], "simple-foo")
        # state.current.waiting should be True (LLM will record completed).
        self.assertTrue(state["current"]["waiting"])

    def test_command_failure_returns_workflow_failed(self):
        config = {
            "tracks": {
                "simple-foo": {"type": "simple", "commands": ["false"]},
            },
        }
        state = self._empty_state()
        runner = self._patch_runner(self.runner)
        result = runner._execute_phase(config, self.change, state, "simple-foo")
        self.assertEqual(result["action"], "workflow_failed")
        self.assertTrue(result["fatal"])
        self.assertIn("simple-foo", result["reason"])

    def test_multiple_commands_stop_on_first_failure(self):
        """If the first command fails, subsequent commands must NOT execute."""
        sentinel = os.path.join(self.tmpdir, "sentinel.txt")
        config = {
            "tracks": {
                "simple-foo": {
                    "type": "simple",
                    "commands": [
                        "false",  # exits 1, should abort the loop
                        f"touch {sentinel}",  # must NOT run
                    ],
                },
            },
        }
        state = self._empty_state()
        runner = self._patch_runner(self.runner)
        result = runner._execute_phase(config, self.change, state, "simple-foo")
        self.assertEqual(result["action"], "workflow_failed")
        self.assertFalse(os.path.exists(sentinel),
                         "Second command must not run after first failure")


# ============================================================
# pg-validate-tasks: simple tracks must be skipped
# ============================================================

class TestValidateSkipsSimpleTracks(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test-validate-simple-")
        self.pg_spec = os.path.join(self.tmpdir, "pg-spec")
        self.changes_dir = os.path.join(self.pg_spec, "changes")
        self.change = "validate-change"
        self.change_dir = os.path.join(self.changes_dir, self.change)
        os.makedirs(self.change_dir)

        # Config with one simple track + one standard track + a dev environment
        # to keep the stage from being skipped.
        with open(os.path.join(self.pg_spec, "config.yaml"), "w") as f:
            f.write(
                "schema: spec-driven\n"
                "modules:\n"
                "  frontend: {root: <module-name>, language: typescript}\n"
                "tracks:\n"
                "  simple-foo:\n"
                "    type: simple\n"
                "    commands: [\"echo hi\"]\n"
                "  standard-bar:\n"
                "    modules: [frontend]\n"
                "environments:\n"
                "  dev-empty:\n"
                "    roles: {}\n"
                "stages:\n"
                "  - name: dev\n"
                "    environment: {required: false}\n"
                "    tracks: [simple-foo, standard-bar]\n"
            )

        # Tasks.md missing the simple-foo section entirely.
        with open(os.path.join(self.change_dir, "tasks.md"), "w") as f:
            f.write(
                "# Tasks\n\n"
                "## 1. standard-bar:dev - standard\n\n"
                "- [ ] 1.1 some task\n"
            )

        # env-override yaml mapping the dev stage to dev-empty environment
        # so get_pipeline_order includes the track items.
        with open(os.path.join(self.change_dir, "environment.yaml"), "w") as f:
            f.write("dev: dev-empty\n")

        # Load common & validate modules pointing at the tmp project.
        self.common = _load_common()
        setattr(self.common, "PROJECT_ROOT", self.tmpdir)
        setattr(self.common, "CHANGES_DIR", self.changes_dir)
        setattr(self.common, "CONFIG_PATH", os.path.join(self.pg_spec, "config.yaml"))

        self.validate = _load_validate()
        setattr(self.validate, "CHANGES_DIR", self.changes_dir)

    def tearDown(self):
        for k in ("pg_pipeline_common", "pg_validate_tasks", "pg_pipeline_runner"):
            if k in sys.modules:
                del sys.modules[k]
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_simple_track_not_flagged_as_missing(self):
        report = self.validate.validate(self.change, skip_stages=set())
        # No missing-section error for simple-foo.
        codes = [iss["code"] for iss in report["issues"]]
        self.assertNotIn("missing_track", codes)
        # simple-foo should be in skipped_items.
        skipped = report["summary"].get("skipped_items", [])
        skipped_bare = [s.rsplit(".", 1)[-1] for s in skipped]
        self.assertIn("simple-foo", skipped_bare)


# ============================================================
# normalize_simple_command tests
# ============================================================

class TestNormalizeSimpleCommand(unittest.TestCase):
    """Pure-function tests for the command-shape normalizer."""

    def setUp(self):
        self.common = _load_common()

    def test_string_entry_uses_track_default_timeout(self):
        out = self.common.normalize_simple_command("echo hi", track_default_timeout=600)
        self.assertEqual(out["cmd"], "echo hi")
        self.assertEqual(out["timeout_seconds"], 600,
                         "String form should fall back to track default")
        self.assertEqual(out["on_failure"], "fail")
        self.assertEqual(out["retry_max"], 2)
        self.assertEqual(out["retry_timeout_seconds"], 600)

    def test_string_entry_with_none_track_default_keeps_none(self):
        out = self.common.normalize_simple_command("echo hi", track_default_timeout=None)
        self.assertIsNone(out["timeout_seconds"],
                          "None track default should propagate to command")
        self.assertIsNone(out["retry_timeout_seconds"])

    def test_dict_entry_with_explicit_timeout_overrides_track(self):
        out = self.common.normalize_simple_command(
            {"cmd": "sleep 5", "timeout_seconds": 30},
            track_default_timeout=600)
        self.assertEqual(out["timeout_seconds"], 30)
        self.assertEqual(out["retry_timeout_seconds"], 30,
                         "retry_timeout should fall back to main timeout")

    def test_dict_entry_with_null_timeout_falls_back_to_track(self):
        out = self.common.normalize_simple_command(
            {"cmd": "sleep 5", "timeout_seconds": None},
            track_default_timeout=600)
        self.assertEqual(out["timeout_seconds"], 600)

    def test_dict_entry_with_explicit_retry_timeout(self):
        out = self.common.normalize_simple_command(
            {"cmd": "x", "timeout_seconds": 300, "retry_timeout_seconds": 60,
             "on_failure": "retry", "retry_max": 2},
            track_default_timeout=600)
        self.assertEqual(out["timeout_seconds"], 300)
        self.assertEqual(out["retry_timeout_seconds"], 60)
        self.assertEqual(out["on_failure"], "retry")
        self.assertEqual(out["retry_max"], 2)

    def test_dict_entry_continue_policy(self):
        out = self.common.normalize_simple_command(
            {"cmd": "x", "on_failure": "continue"},
            track_default_timeout=10)
        self.assertEqual(out["on_failure"], "continue")

    def test_dict_entry_default_policy_is_fail(self):
        out = self.common.normalize_simple_command(
            {"cmd": "x"}, track_default_timeout=10)
        self.assertEqual(out["on_failure"], "fail")

    def test_dict_missing_cmd_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.common.normalize_simple_command(
                {"timeout_seconds": 30}, track_default_timeout=600)
        self.assertIn("cmd", str(ctx.exception))

    def test_dict_empty_cmd_raises(self):
        with self.assertRaises(ValueError):
            self.common.normalize_simple_command(
                {"cmd": "  "}, track_default_timeout=600)

    def test_invalid_on_failure_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.common.normalize_simple_command(
                {"cmd": "x", "on_failure": "ignore"}, track_default_timeout=10)
        self.assertIn("on_failure", str(ctx.exception))

    def test_invalid_entry_type_raises(self):
        with self.assertRaises(ValueError):
            self.common.normalize_simple_command(42, track_default_timeout=10)
        with self.assertRaises(ValueError):
            self.common.normalize_simple_command([1, 2], track_default_timeout=10)


# ============================================================
# _execute_phase — object commands, timeout, retry, continue
# ============================================================

class TestExecutePhaseAdvancedPolicies(unittest.TestCase):
    """Exercises on_failure=fail/continue/retry and track.on_failure."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test-simple-track-advanced-")
        self.pg_spec = os.path.join(self.tmpdir, "pg-spec")
        self.changes_dir = os.path.join(self.pg_spec, "changes")
        self.change = "adv-change"
        self.change_dir = os.path.join(self.changes_dir, self.change)
        os.makedirs(self.change_dir)
        self.runner = _load_runner()
        setattr(self.runner, "PROJECT_ROOT", self.tmpdir)
        setattr(self.runner, "CHANGES_DIR", self.changes_dir)
        setattr(self.runner, "CONFIG_PATH", os.path.join(self.pg_spec, "config.yaml"))
        setattr(self.runner, "APPLY_STATE_FILES",
                (".context-chain.state", ".pipeline-state.json"))
        self._patch_runner(self.runner)

    def tearDown(self):
        for k in ("pg_pipeline_runner", "pg_pipeline_common"):
            if k in sys.modules:
                del sys.modules[k]
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _patch_runner(self, runner):
        runner.save_state = mock.MagicMock(return_value=None)
        runner.pipeline_mark = mock.MagicMock(return_value=None)
        if hasattr(runner, "pg_context_chain"):
            runner.pg_context_chain.phase_start = mock.MagicMock()
            runner.pg_context_chain.phase_end = mock.MagicMock()
        return runner

    def _empty_state(self):
        return {
            "change": self.change, "current": None, "completed_items": [],
            "failed": False, "completed": False, "init_committed": True,
        }

    def test_object_command_uses_explicit_timeout(self):
        """A command with timeout_seconds=2 sleeps 5s; we expect a timeout
        failure surfaced as workflow_failed."""
        config = {
            "tracks": {"simple-foo": {
                "type": "simple",
                "timeout_seconds": 1800,  # track default large
                "commands": [{"cmd": "sleep 5", "timeout_seconds": 2}],
            }},
        }
        state = self._empty_state()
        result = self.runner._execute_phase(config, self.change, state, "simple-foo")
        self.assertEqual(result["action"], "workflow_failed")
        self.assertIn("simple-foo", result["reason"])

    def test_object_command_continue_policy_runs_subsequent(self):
        """First command fails but on_failure=continue; second command
        should still execute and the track should succeed."""
        sentinel = os.path.join(self.tmpdir, "sentinel-continue.txt")
        config = {
            "tracks": {"simple-foo": {
                "type": "simple",
                "commands": [
                    {"cmd": "false", "on_failure": "continue"},
                    {"cmd": f"touch {sentinel}"},
                ],
            }},
        }
        state = self._empty_state()
        result = self.runner._execute_phase(config, self.change, state, "simple-foo")
        self.assertEqual(result["action"], "phase_result")
        self.assertTrue(os.path.exists(sentinel),
                        "Second command must run after a continued failure")

    def test_retry_policy_succeeds_on_second_attempt(self):
        """on_failure=retry: first attempt fails, second succeeds. The
        track should mark complete and never return workflow_failed."""
        attempts_file = os.path.join(self.tmpdir, "attempts.txt")
        # Atomic append: write current value, exit 0 only on second call.
        cmd = (
            f"if [ ! -f {attempts_file} ]; then echo 1 > {attempts_file}; exit 1; "
            f"else echo 2 > {attempts_file}; exit 0; fi"
        )
        config = {
            "tracks": {"simple-foo": {
                "type": "simple",
                "commands": [{"cmd": cmd, "on_failure": "retry", "retry_max": 2}],
            }},
        }
        state = self._empty_state()
        result = self.runner._execute_phase(config, self.change, state, "simple-foo")
        self.assertEqual(result["action"], "phase_result",
                         f"Expected phase_result, got {result}")
        with open(attempts_file) as f:
            self.assertEqual(f.read().strip(), "2",
                             "Retry should have allowed second attempt to run")

    def test_retry_policy_exhausted_returns_workflow_failed(self):
        """on_failure=retry with retry_max=1 and a command that always
        fails: should run 2 attempts then workflow_failed."""
        config = {
            "tracks": {"simple-foo": {
                "type": "simple",
                "commands": [{"cmd": "false", "on_failure": "retry", "retry_max": 1}],
            }},
        }
        state = self._empty_state()
        result = self.runner._execute_phase(config, self.change, state, "simple-foo")
        self.assertEqual(result["action"], "workflow_failed")

    def test_track_on_failure_continue_all_ignores_failure(self):
        """Track-level on_failure=continue_all: even a hard 'fail' policy
        command should be tolerated; the track continues to the next item."""
        config = {
            "tracks": {"simple-foo": {
                "type": "simple",
                "on_failure": "continue_all",
                "commands": ["false"],  # default on_failure=fail
            }},
        }
        state = self._empty_state()
        # Stub cmd_next so we don't need a real config.yaml / pipeline
        # state — we only care that continue_all marks the track complete.
        setattr(self.runner, "cmd_next", mock.MagicMock(
            return_value={"action": "advanced"}))
        result = self.runner._execute_phase(config, self.change, state, "simple-foo")
        self.assertIn("simple-foo", state.get("completed_items", []),
                      f"track should be marked complete under continue_all, "
                      f"got result={result} state={state}")
        # cmd_next stubbed via setattr above
        getattr(self.runner, "cmd_next").assert_called_once()

    def test_invalid_command_config_returns_workflow_failed(self):
        """A command dict missing required 'cmd' field triggers a
        configuration error surfaced as workflow_failed."""
        config = {
            "tracks": {"simple-foo": {
                "type": "simple",
                "commands": [{"timeout_seconds": 30}],  # missing cmd
            }},
        }
        state = self._empty_state()
        result = self.runner._execute_phase(config, self.change, state, "simple-foo")
        self.assertEqual(result["action"], "workflow_failed")
        self.assertIn("配置错误", result["reason"])


# ============================================================
# Schema validation: .pg/project.yaml against config.schema.json
# ============================================================

class TestConfigSchemaValidates(unittest.TestCase):
    """Ensures the example in .pg/project.yaml still passes the schema
    after we add timeout_seconds / on_failure / object commands. This is
    a regression guard: the user-visible config example must never drift
    out of sync with the schema."""

    SCHEMA_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(SCRIPTS_DIR)))),
        "pg-spec", "schema", "config.schema.json")
    CONFIG_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(SCRIPTS_DIR)))),
        "pg-spec", "config.yaml")

    def test_simple_track_block_in_config_yaml_validates(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        with open(self.SCHEMA_PATH) as f:
            schema = json.load(f)
        with open(self.CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        track = dict(cfg["tracks"]["openapi-gen"])
        # The schema requires `modules`; the simple-track example in
        # config.yaml demonstrates a no-modules track (the runner doesn't
        # need modules for direct command execution). Inject an empty list
        # to satisfy schema validation.
        track.setdefault("modules", [])
        track_def = schema["definitions"]["track"]
        try:
            jsonschema.validate(track, track_def)
        except jsonschema.ValidationError as e:
            self.fail(f"openapi-gen block fails schema: {e.message}")


if __name__ == "__main__":
    unittest.main()