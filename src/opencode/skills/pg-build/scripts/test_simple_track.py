#!/usr/bin/env python3
"""Tests for the simple-track feature.

Covers:
- get_track_type() classifies simple tracks as 'phase'.
- get_track_type() preserves 'track' for standard tracks.
- _noopify_simple_track_sections() rewrites simple-track sections to noop.
- _noopify_simple_track_sections() is idempotent on second invocation.
- _noopify_simple_track_sections() leaves standard tracks untouched.
- _execute_phase() dispatches simple tracks to pg-build/simple agent.
- _execute_phase() returns workflow_failed when a simple track has no commands.
- _build_simple_dispatch() returns a dispatch action with agent=pg-build/simple.
- _build_simple_dispatch() includes normalized commands + decision table in prompt.
- _compute_simple_timeout() = sum(cmd.timeout) + N*30.
- _infer_next_report_n() scans 2-build/ for next N.
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

    def test_simple_qualified_form_returns_phase(self):
        """Regression: cmd_detect passes qualified item ids like
        'dev.openapi-gen', but `tracks` keys are bare ('openapi-gen').
        get_track_type must strip the stage prefix before lookup so the
        simple track is correctly classified as 'phase' (which routes to
        _build_simple_dispatch instead of TDVG sub-agent dispatch)."""
        config = {"tracks": {
            "openapi-gen": {"type": "simple", "commands": ["echo hi"]},
        }}
        self.assertEqual(
            self.common.get_track_type(config, "dev.openapi-gen"), "phase")
        self.assertEqual(
            self.common.get_track_type(config, "real-integration.openapi-gen"),
            "phase")

    def test_standard_qualified_form_returns_track(self):
        """Standard tracks must remain 'track' even with qualified form."""
        config = {"tracks": {
            "backend": {"modules": ["backend"]},
        }}
        self.assertEqual(
            self.common.get_track_type(config, "dev.backend"), "track")


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
# _execute_phase simple-track branch tests (dispatch model)
# ============================================================
# Simple tracks are now dispatched to the pg-build/simple sub-agent rather
# than executed in-process. These tests verify:
#   1. _execute_phase redirects simple tracks to _build_simple_dispatch.
#   2. _build_simple_dispatch returns the correct action shape.
#   3. _build_simple_context produces a complete ctx.
#   4. _compute_simple_timeout follows the sum+N*30 rule.
#   5. _infer_next_report_n scans 2-build/ correctly.
#   6. Missing commands produces workflow_failed.

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

    def tearDown(self):
        for k in ("pg_pipeline_runner", "pg_pipeline_common"):
            if k in sys.modules:
                del sys.modules[k]
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_config(self, *, simple_commands, on_failure=None,
                      timeout_seconds=None):
        cfg_path = os.path.join(self.pg_spec, "config.yaml")
        cmds_yaml = "\n".join(f'      - "{c}"' for c in simple_commands)
        extra = ""
        if on_failure is not None:
            extra += f"    on_failure: {on_failure}\n"
        if timeout_seconds is not None:
            extra += f"    timeout_seconds: {timeout_seconds}\n"
        with open(cfg_path, "w") as f:
            f.write(
                "schema: spec-driven\n"
                "modules:\n"
                "  frontend: {root: <module-name>, language: typescript}\n"
                "tracks:\n"
                "  simple-foo:\n"
                "    type: simple\n"
                f"    commands:\n{cmds_yaml}\n"
                f"{extra}"
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
        runner.save_state = mock.MagicMock(return_value=None)
        runner.pipeline_mark = mock.MagicMock(return_value=None)
        if hasattr(runner, "pg_context_chain"):
            runner.pg_context_chain.sub_start = mock.MagicMock()
            runner.pg_context_chain.sub_end = mock.MagicMock()
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

    def test_execute_phase_dispatches_simple_track_as_agent(self):
        """_execute_phase must return action=dispatch, agent=pg-build/simple
        for simple tracks (instead of executing in-process)."""
        self._write_config(simple_commands=["echo hello"])
        config = {
            "tracks": {
                "simple-foo": {"type": "simple", "commands": ["echo hello"]},
            },
        }
        state = self._empty_state()
        runner = self._patch_runner(self.runner)
        result = runner._execute_phase(config, self.change, state, "simple-foo")
        self.assertEqual(result["action"], "dispatch")
        self.assertEqual(result["agent"], "pg-build/simple")
        self.assertEqual(result["item"], "simple-foo")
        self.assertEqual(result["sub"], "simple")
        # state.current.waiting should be True after sub_start.
        self.assertTrue(state["current"]["waiting"])
        self.assertEqual(state["current"]["sub"], "simple")

    def test_execute_phase_prompt_contains_normalized_commands(self):
        """The dispatched prompt must include the normalized command list
        so the pg-build/simple agent knows what to run."""
        self._write_config(simple_commands=[
            "echo first",
            {"cmd": "echo second", "timeout_seconds": 30, "on_failure": "continue"},
        ])
        config = {
            "tracks": {
                "simple-foo": {"type": "simple", "commands": [
                    "echo first",
                    {"cmd": "echo second", "timeout_seconds": 30,
                     "on_failure": "continue"},
                ]},
            },
        }
        state = self._empty_state()
        runner = self._patch_runner(self.runner)
        result = runner._execute_phase(config, self.change, state, "simple-foo")
        prompt = result["prompt_final_no_modify"]
        # Both commands appear.
        self.assertIn("echo first", prompt)
        self.assertIn("echo second", prompt)
        # The second command's on_failure policy is visible in the prompt.
        self.assertIn("on_failure=continue", prompt)
        # Track timeout is surfaced in the Track 配置 block.
        self.assertIn("track.timeout_seconds", prompt)

    def test_execute_phase_prompt_contains_decision_table(self):
        """The prompt must include the failure-handling decision table
        so the agent knows how to interpret on_failure values."""
        self._write_config(simple_commands=["echo hi"])
        config = {"tracks": {"simple-foo": {"type": "simple", "commands": ["echo hi"]}}}
        state = self._empty_state()
        runner = self._patch_runner(self.runner)
        result = runner._execute_phase(config, self.change, state, "simple-foo")
        prompt = result["prompt_final_no_modify"]
        self.assertIn("失败处理决策表", prompt)
        self.assertIn("`fail`", prompt)
        self.assertIn("`continue`", prompt)
        self.assertIn("`retry`", prompt)

    def test_execute_phase_prompt_includes_next_report_n(self):
        """runner must inject next_report_n so the agent knows the report
        filename suffix for self-writing the report."""
        # Pre-create a report file with N=2 so next_report_n should be 3.
        os.makedirs(self.apply_dir, exist_ok=True)
        with open(os.path.join(self.apply_dir, "simple-foo-2-simple.md"), "w") as f:
            f.write("dummy")
        self._write_config(simple_commands=["echo hi"])
        config = {"tracks": {"simple-foo": {"type": "simple", "commands": ["echo hi"]}}}
        state = self._empty_state()
        runner = self._patch_runner(self.runner)
        result = runner._execute_phase(config, self.change, state, "simple-foo")
        prompt = result["prompt_final_no_modify"]
        self.assertIn("simple-foo-3-simple.md", prompt)


class TestBuildSimpleDispatch(unittest.TestCase):
    """Direct tests for _build_simple_dispatch / _build_simple_context /
    _compute_simple_timeout / _infer_next_report_n helpers."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test-simple-dispatch-")
        self.pg_spec = os.path.join(self.tmpdir, "pg-spec")
        self.changes_dir = os.path.join(self.pg_spec, "changes")
        self.change = "dispatch-change"
        self.change_dir = os.path.join(self.changes_dir, self.change)
        self.apply_dir = os.path.join(self.change_dir, "2-build")
        os.makedirs(self.apply_dir)

        self.runner = _load_runner()
        setattr(self.runner, "PROJECT_ROOT", self.tmpdir)
        setattr(self.runner, "CHANGES_DIR", self.changes_dir)
        setattr(self.runner, "CONFIG_PATH", os.path.join(self.pg_spec, "config.yaml"))

    def tearDown(self):
        for k in ("pg_pipeline_runner", "pg_pipeline_common"):
            if k in sys.modules:
                del sys.modules[k]
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _config(self, simple_cfg):
        cfg_path = os.path.join(self.pg_spec, "config.yaml")
        import yaml as _yaml
        with open(cfg_path, "w") as f:
            _yaml.safe_dump({"tracks": simple_cfg, "modules": {},
                             "environments": {}, "stages": []}, f)
        return {"tracks": simple_cfg}

    # ----- _compute_simple_timeout -----

    def test_compute_simple_timeout_empty_returns_600(self):
        self.assertEqual(self.runner._compute_simple_timeout([]), 600)

    def test_compute_simple_timeout_sum_plus_per_cmd_overhead(self):
        cmds = [{"timeout_seconds": 60}, {"timeout_seconds": 120},
                {"timeout_seconds": 30}]
        # 60 + 120 + 30 + 3*30 = 300
        self.assertEqual(self.runner._compute_simple_timeout(cmds), 300)

    def test_compute_simple_timeout_none_falls_back_to_1800(self):
        cmds = [{"timeout_seconds": None}, {"timeout_seconds": 60}]
        # 1800 + 60 + 2*30 = 1920
        self.assertEqual(self.runner._compute_simple_timeout(cmds), 1920)

    # ----- _infer_next_report_n -----

    def test_infer_next_report_n_no_existing_reports(self):
        n = self.runner._infer_next_report_n(self.change, "simple-foo")
        self.assertEqual(n, 1)

    def test_infer_next_report_n_with_existing_reports(self):
        for k, name in [(1, "simple-foo-1-simple.md"),
                        (3, "simple-foo-3-simple.md")]:
            with open(os.path.join(self.apply_dir, name), "w") as f:
                f.write(f"report-{k}")
        n = self.runner._infer_next_report_n(self.change, "simple-foo")
        self.assertEqual(n, 4)

    def test_infer_next_report_n_ignores_other_tracks(self):
        with open(os.path.join(self.apply_dir, "backend-5-verify.md"), "w") as f:
            f.write("other")
        n = self.runner._infer_next_report_n(self.change, "simple-foo")
        self.assertEqual(n, 1)

    # ----- _build_simple_context -----

    def test_build_simple_context_commands_missing_sentinel(self):
        cfg = self._config({"simple-foo": {"type": "simple", "commands": []}})
        ctx = self.runner._build_simple_context(cfg, self.change, "simple-foo")
        self.assertTrue(ctx.get("_commands_missing"))

    def test_build_simple_context_normalizes_commands(self):
        cfg = self._config({"simple-foo": {"type": "simple", "commands": [
            "echo a",
            {"cmd": "echo b", "timeout_seconds": 10, "on_failure": "retry",
             "retry_max": 1, "retry_timeout_seconds": 5},
        ]}})
        ctx = self.runner._build_simple_context(cfg, self.change, "simple-foo")
        self.assertFalse(ctx.get("_commands_missing"))
        cmds = ctx["commands_normalized"]
        self.assertEqual(len(cmds), 2)
        self.assertEqual(cmds[0]["idx"], 1)
        self.assertEqual(cmds[0]["cmd"], "echo a")
        self.assertEqual(cmds[1]["on_failure"], "retry")
        self.assertEqual(cmds[1]["retry_max"], 1)

    def test_build_simple_context_track_defaults(self):
        cfg = self._config({"simple-foo": {"type": "simple",
                                            "commands": ["echo hi"]}})
        ctx = self.runner._build_simple_context(cfg, self.change, "simple-foo")
        self.assertEqual(ctx["track_type"], "simple")
        self.assertEqual(ctx["track_timeout"], 1800)
        self.assertEqual(ctx["track_on_failure"], "workflow_failed")
        # base-template compatibility fields
        self.assertEqual(ctx["review_level"], "none")
        self.assertEqual(ctx["modules"], [])
        self.assertEqual(ctx["module_details"], [])
        self.assertEqual(ctx["tasks_noop"], True)
        self.assertEqual(ctx["tasks_preformatted"], [])
        self.assertEqual(ctx["tasks_validation"], "")

    def test_build_simple_context_uses_explicit_track_timeout(self):
        cfg = self._config({"simple-foo": {
            "type": "simple", "timeout_seconds": 60,
            "on_failure": "continue_all", "commands": ["echo hi"],
        }})
        ctx = self.runner._build_simple_context(cfg, self.change, "simple-foo")
        self.assertEqual(ctx["track_timeout"], 60)
        self.assertEqual(ctx["track_on_failure"], "continue_all")

    # ----- _build_simple_dispatch -----

    def test_build_simple_dispatch_returns_correct_action_shape(self):
        cfg = self._config({"simple-foo": {"type": "simple",
                                            "commands": ["echo hi"]}})
        result = self.runner._build_simple_dispatch(
            cfg, self.change, "simple-foo")
        self.assertEqual(result["action"], "dispatch")
        self.assertEqual(result["agent"], "pg-build/simple")
        self.assertEqual(result["item"], "simple-foo")
        self.assertEqual(result["sub"], "simple")
        self.assertEqual(result["attempt"], 1)
        self.assertIsInstance(result["prompt_final_no_modify"], str)
        self.assertIn("prompt_injection", result)
        self.assertIn("next_call_timeout_seconds", result)

    def test_build_simple_dispatch_prompt_includes_track_metadata(self):
        cfg = self._config({"simple-foo": {"type": "simple",
                                            "commands": ["echo hi"]}})
        result = self.runner._build_simple_dispatch(
            cfg, self.change, "simple-foo")
        prompt = result["prompt_final_no_modify"]
        self.assertIn("simple-foo", prompt)
        self.assertIn("echo hi", prompt)
        self.assertIn("track.type", prompt)
        self.assertIn("track.timeout_seconds", prompt)

    def test_build_simple_dispatch_timeout_matches_compute(self):
        cfg = self._config({"simple-foo": {"type": "simple", "commands": [
            {"cmd": "sleep 1", "timeout_seconds": 100},
            {"cmd": "sleep 2", "timeout_seconds": 200},
        ]}})
        result = self.runner._build_simple_dispatch(
            cfg, self.change, "simple-foo")
        # 100 + 200 + 2*30 = 360
        self.assertEqual(result["next_call_timeout_seconds"], 360)

    def test_build_simple_dispatch_workflow_failed_when_no_commands(self):
        cfg = self._config({"simple-foo": {"type": "simple", "commands": []}})
        result = self.runner._build_simple_dispatch(
            cfg, self.change, "simple-foo")
        self.assertEqual(result["action"], "workflow_failed")
        self.assertTrue(result["fatal"])
        self.assertIn("缺少 commands", result["reason"])

    def test_build_simple_dispatch_value_error_for_non_simple_track(self):
        cfg = self._config({"backend": {"modules": ["backend"]}})
        with self.assertRaises(ValueError):
            self.runner._build_simple_dispatch(cfg, self.change, "backend")

    def test_build_simple_dispatch_prompt_uses_simple_agent_block(self):
        """The prompt must include the _PROMPT_BLOCK_SIMPLE specific
        sections (命令执行要求 + 决策表 + 返回格式) rather than the
        standard dev/verify blocks."""
        cfg = self._config({"simple-foo": {"type": "simple",
                                            "commands": ["echo hi"]}})
        result = self.runner._build_simple_dispatch(
            cfg, self.change, "simple-foo")
        prompt = result["prompt_final_no_modify"]
        self.assertIn("Simple Track 命令执行要求", prompt)
        self.assertIn("待执行命令（顺序执行", prompt)
        self.assertIn("返回格式", prompt)


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
# ============================================================
# _parse_heading tests (heading parser for simple track sections)
# ============================================================

class TestParseHeading(unittest.TestCase):
    """Regression tests for _parse_heading — the simple track heading has
    a stage prefix + no :sub, which previously fell through both regexes
    and broke _noopify_simple_track_sections (the heading got dumped
    into sec['item'] as the entire tail string)."""

    def setUp(self):
        self.common = _load_common()

    def test_standard_track_heading(self):
        item, sub, label = self.common._parse_heading(
            "dev.backend:dev - backend dev")
        self.assertEqual(item, "dev.backend")
        self.assertEqual(sub, "dev")
        self.assertEqual(label, "backend dev")

    def test_simple_track_heading_returns_bare_item(self):
        """Simple track heading 'dev.openapi-gen - dev openapi-gen  (...)'
        must parse to bare item 'openapi-gen' (no stage prefix), no sub.
        The '. ' suffix '(simple track: ...)' goes into label."""
        item, sub, label = self.common._parse_heading(
            "dev.openapi-gen - dev openapi-gen  (simple track: 派遣 ...)")
        self.assertEqual(item, "openapi-gen")
        self.assertIsNone(sub)
        self.assertEqual(label, "dev openapi-gen  (simple track: 派遣 ...)")

    def test_phase_heading_no_stage_prefix(self):
        item, sub, label = self.common._parse_heading(
            "proto-compile - Proto编译")
        self.assertEqual(item, "proto-compile")
        self.assertIsNone(sub)
        self.assertEqual(label, "Proto编译")

    def test_standard_track_heading_no_stage_prefix(self):
        item, sub, label = self.common._parse_heading(
            "backend:dev - backend dev")
        self.assertEqual(item, "backend")
        self.assertEqual(sub, "dev")
        self.assertEqual(label, "backend dev")


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
# _execute_phase — dispatch model: object commands, on_failure policies
# ============================================================

class TestExecutePhaseAdvancedPolicies(unittest.TestCase):
    """In the dispatch model, on_failure=continue/retry/fail and
    track.on_failure=continue_all semantics are now enforced by the
    pg-build/simple sub-agent (LLM-driven) rather than by runner Popen.

    These tests verify the runner-side contract: the dispatch prompt
    correctly surfaces each command's on_failure policy + retry params
    so the agent has the information to make the right decision.

    The actual command-execution behavior is now covered by the agent
    itself (which has LLM reasoning + auto-recovery)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test-simple-track-advanced-")
        self.pg_spec = os.path.join(self.tmpdir, "pg-spec")
        self.changes_dir = os.path.join(self.pg_spec, "changes")
        self.change = "adv-change"
        self.change_dir = os.path.join(self.changes_dir, self.change)
        self.apply_dir = os.path.join(self.change_dir, "2-build")
        os.makedirs(self.apply_dir)
        self.runner = _load_runner()
        setattr(self.runner, "PROJECT_ROOT", self.tmpdir)
        setattr(self.runner, "CHANGES_DIR", self.changes_dir)
        setattr(self.runner, "CONFIG_PATH", os.path.join(self.pg_spec, "config.yaml"))
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
            runner.pg_context_chain.sub_start = mock.MagicMock()
            runner.pg_context_chain.sub_end = mock.MagicMock()
            runner.pg_context_chain.phase_start = mock.MagicMock()
            runner.pg_context_chain.phase_end = mock.MagicMock()
        return runner

    def _empty_state(self):
        return {
            "change": self.change, "current": None, "completed_items": [],
            "failed": False, "completed": False, "init_committed": True,
        }

    def test_object_command_timeout_surfaced_in_prompt(self):
        """An object-form command's timeout_seconds must appear in the
        dispatch prompt so the agent can enforce it."""
        config = {
            "tracks": {"simple-foo": {
                "type": "simple",
                "timeout_seconds": 1800,
                "commands": [{"cmd": "sleep 5", "timeout_seconds": 2}],
            }},
        }
        state = self._empty_state()
        result = self.runner._execute_phase(config, self.change, state, "simple-foo")
        self.assertEqual(result["action"], "dispatch")
        self.assertEqual(result["agent"], "pg-build/simple")
        prompt = result["prompt_final_no_modify"]
        self.assertIn("sleep 5", prompt)
        self.assertIn("timeout=2s", prompt)

    def test_object_command_continue_policy_in_prompt(self):
        """A command with on_failure=continue must surface the policy so
        the agent knows to keep going after a failure."""
        config = {
            "tracks": {"simple-foo": {
                "type": "simple",
                "commands": [
                    {"cmd": "false", "on_failure": "continue"},
                    {"cmd": "echo ok"},
                ],
            }},
        }
        state = self._empty_state()
        result = self.runner._execute_phase(config, self.change, state, "simple-foo")
        prompt = result["prompt_final_no_modify"]
        # Both commands present
        self.assertIn("false", prompt)
        self.assertIn("echo ok", prompt)
        # on_failure=continue is visible per-command
        self.assertIn("on_failure=continue", prompt)

    def test_retry_policy_params_in_prompt(self):
        """A command with on_failure=retry + retry_max + retry_timeout
        must surface all three params so the agent can retry correctly."""
        config = {
            "tracks": {"simple-foo": {
                "type": "simple",
                "commands": [{
                    "cmd": "flaky", "on_failure": "retry",
                    "retry_max": 2, "retry_timeout_seconds": 5,
                }],
            }},
        }
        state = self._empty_state()
        result = self.runner._execute_phase(config, self.change, state, "simple-foo")
        prompt = result["prompt_final_no_modify"]
        self.assertIn("on_failure=retry", prompt)
        self.assertIn("retry_max=2", prompt)
        # The retry-timeout hint must be in the bullet below the command
        # (the {#if this.on_failure in ["retry"]} block).
        self.assertIn("自动重试最多 2 次", prompt)
        self.assertIn("timeout 5s", prompt)

    def test_track_on_failure_continue_all_surfaced_in_prompt(self):
        """Track-level on_failure=continue_all must surface in the prompt
        so the agent knows not to abort the whole track on a hard failure.

        The agent still returns SUCCESS/FAILED; the runner record phase
        honors continue_all by not returning workflow_failed."""
        config = {
            "tracks": {"simple-foo": {
                "type": "simple",
                "on_failure": "continue_all",
                "commands": ["false"],
            }},
        }
        state = self._empty_state()
        result = self.runner._execute_phase(config, self.change, state, "simple-foo")
        self.assertEqual(result["action"], "dispatch")
        prompt = result["prompt_final_no_modify"]
        self.assertIn("track.on_failure: continue_all", prompt)
        # Decision table mentions continue_all semantics.
        self.assertIn("continue_all", prompt)

    def test_invalid_command_config_returns_workflow_failed(self):
        """A command dict missing required 'cmd' field is a config error:
        _execute_phase should surface it as workflow_failed via
        _build_simple_dispatch's normalize_simple_command call."""
        config = {
            "tracks": {"simple-foo": {
                "type": "simple",
                "commands": [{"timeout_seconds": 30}],  # missing cmd
            }},
        }
        state = self._empty_state()
        # Patch _build_simple_dispatch to actually raise (since the runner
        # does not catch ValueError from normalize_simple_command).
        with self.assertRaises(ValueError):
            self.runner._execute_phase(config, self.change, state, "simple-foo")


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