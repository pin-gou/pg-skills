#!/usr/bin/env python3
"""Tests for pg-parse-config.py fix_issue segment support.

Verifies that:
  - pg-fix-issue workflow exposes fix_issue segment
  - Other workflows do NOT expose fix_issue (separation of concerns)
  - fix_issue values from config.yaml flow through filter_by_workflow
  - Absent fix_issue segment yields no key (defensive: SKILL.md has defaults)
  - fix_issue shape matches documented SKILL.md fields
"""

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


def _find_project_root() -> Path:
    """Walk up from __file__ + cwd + PG_PROJECT_ROOT env var.

    Handles symlinks (.opencode/...) AND hardlinks (upstream pg-skills
    without .pg/project.yaml). Mirrors test_v3_invoke_hook_migration.py
    for consistency.
    """
    env_root = os.environ.get("PG_PROJECT_ROOT")
    if env_root and (Path(env_root) / ".pg" / "project.yaml").is_file():
        return Path(env_root)
    candidates = [Path(__file__).resolve().parent, Path.cwd()]
    seen = set()
    for start in candidates:
        p = start
        for _ in range(15):
            if p in seen:
                break
            seen.add(p)
            if (p / ".pg" / "project.yaml").is_file():
                return p
            parent = p.parent
            if parent == p:
                break
            p = parent
    raise RuntimeError(
        f"Cannot find .pg/project.yaml from {start}. "
        f"Set PG_PROJECT_ROOT env var."
    )


PROJECT_ROOT = _find_project_root()
SCRIPT_PATH = PROJECT_ROOT / ".pg" / "skills" / "src" / "opencode" / "scripts" / "pg-parse-config.py"


def load_parser() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("pg_parse_config", str(SCRIPT_PATH))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_temp_config(yaml_text: str) -> Path:
    fd, p = tempfile.mkstemp(suffix=".yaml", text=True)
    os.write(fd, yaml_text.encode("utf-8"))
    os.close(fd)
    return Path(p)


SAMPLE_CONFIG = """\
schema: spec-driven

modules:
  backend:
    root: <module-name>
    language: java
    build: cd <module-name> && mvn -DskipTests package install -q
    test:
      unit: cd <module-name> && mvn test

environments:
  dev-local:
    description: "本机全栈开发"
    roles:
      backend:
        instances:
          - {name: backend-1, host: localhost, port: 9080}

tracks:
  backend:
    modules: [backend]
    max_fix_retries: 5

stages:
  - name: dev-backend
    tracks: [backend]

fix_issue:
  max_iteration_count: 7
  partial_success_threshold: 0.8
  ask_environment_choice: true
  ask_prepare_env: false
  ask_clean_env: true
  allow_manual_verification: true
  escalation_artifacts:
    - diag_logs
    - call_chain_analysis
    - phase2_output
"""


class FixIssueWorkflowKeysTest(unittest.TestCase):
    """Verify WORKFLOW_KEYS exposes fix_issue only to pg-fix-issue."""

    def setUp(self):
        self.mod = load_parser()

    def test_pg_fix_issue_workflow_exposes_fix_issue(self):
        keys = self.mod.WORKFLOW_KEYS["pg-fix-issue"]
        self.assertIn("fix_issue", keys)

    def test_pg_fix_issue_workflow_exposes_required_segments(self):
        keys = self.mod.WORKFLOW_KEYS["pg-fix-issue"]
        for required in ("modules", "environments", "tracks", "stages", "fix_issue"):
            self.assertIn(required, keys,
                          f"pg-fix-issue must expose {required}")

    def test_other_workflows_do_not_expose_fix_issue(self):
        for wf in ("pg-build", "pg-propose", "pg-quick-build",
                   "pg-verify-and-merge"):
            keys = self.mod.WORKFLOW_KEYS.get(wf, [])
            self.assertNotIn("fix_issue", keys,
                             f"{wf} must NOT expose fix_issue")


class FixIssueFilterTest(unittest.TestCase):
    """Verify filter_by_workflow yields fix_issue values when present."""

    def setUp(self):
        self.mod = load_parser()
        self.tmp = write_temp_config(SAMPLE_CONFIG)
        self._patcher = patch.object(self.mod, "CONFIG_PATH", str(self.tmp))
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self.tmp.unlink(missing_ok=True)

    def test_pg_fix_issue_sees_fix_issue_segment(self):
        data = self.mod.load()
        filtered = self.mod.filter_by_workflow(data, "pg-fix-issue")
        self.assertIn("fix_issue", filtered)

    def test_fix_issue_values_pass_through(self):
        data = self.mod.load()
        filtered = self.mod.filter_by_workflow(data, "pg-fix-issue")
        fi = filtered["fix_issue"]
        self.assertEqual(fi["max_iteration_count"], 7)
        self.assertEqual(fi["partial_success_threshold"], 0.8)
        self.assertTrue(fi["ask_environment_choice"])
        self.assertFalse(fi["ask_prepare_env"])
        self.assertTrue(fi["ask_clean_env"])
        self.assertTrue(fi["allow_manual_verification"])
        self.assertEqual(fi["escalation_artifacts"],
                         ["diag_logs", "call_chain_analysis", "phase2_output"])

    def test_pg_fix_issue_still_sees_modules_environments_tracks_stages(self):
        """Regression: adding fix_issue must not break existing segment exposure."""
        data = self.mod.load()
        filtered = self.mod.filter_by_workflow(data, "pg-fix-issue")
        self.assertIn("modules", filtered)
        self.assertIn("environments", filtered)
        self.assertIn("tracks", filtered)
        self.assertIn("stages", filtered)
        self.assertEqual(filtered["modules"]["backend"]["root"], "<module-name>")
        self.assertEqual(filtered["tracks"]["backend"]["modules"], ["backend"])
        self.assertEqual(filtered["stages"][0]["name"], "dev-backend")


class FixIssueDefaultsTest(unittest.TestCase):
    """When fix_issue is absent, SKILL.md provides defaults (parser omits key)."""

    def setUp(self):
        self.mod = load_parser()
        minimal = """\
schema: spec-driven
modules:
  backend: {root: <module-name>, language: java}
environments:
  dev-local: {roles: {backend: {instances: [{name: b1, host: l, port: 9080}]}}}
tracks:
  backend: {modules: [backend]}
stages:
  - {name: s, tracks: [backend]}
"""
        self.tmp = write_temp_config(minimal)
        self._patcher = patch.object(self.mod, "CONFIG_PATH", str(self.tmp))
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self.tmp.unlink(missing_ok=True)

    def test_absent_fix_issue_does_not_appear(self):
        data = self.mod.load()
        filtered = self.mod.filter_by_workflow(data, "pg-fix-issue")
        self.assertNotIn("fix_issue", filtered)


class FixIssueShapeTest(unittest.TestCase):
    """Light shape validation mirroring what SKILL.md documents.

    Mirrors fix_issue defaults from pg-spec-deprecated/schema/config.schema.json so
    any schema drift is caught here too.
    """

    EXPECTED_KEYS = {
        "max_iteration_count",
        "partial_success_threshold",
        "ask_environment_choice",
        "ask_prepare_env",
        "ask_clean_env",
        "allow_manual_verification",
        "escalation_artifacts",
    }

    VALID_ESCALATION_ARTIFACTS = {
        "diag_logs",
        "call_chain_analysis",
        "phase2_output",
        "executor_json_history",
        "git_diff_state",
    }

    def setUp(self):
        self.mod = load_parser()
        self.tmp = write_temp_config(SAMPLE_CONFIG)
        self._patcher = patch.object(self.mod, "CONFIG_PATH", str(self.tmp))
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self.tmp.unlink(missing_ok=True)

    def test_fix_issue_has_expected_keys(self):
        data = self.mod.load()
        fi = data["fix_issue"]
        self.assertTrue(self.EXPECTED_KEYS.issuperset(fi.keys()),
                        f"unexpected keys in fix_issue: {set(fi.keys()) - self.EXPECTED_KEYS}")

    def test_max_iteration_count_is_positive_int(self):
        data = self.mod.load()
        self.assertIsInstance(data["fix_issue"]["max_iteration_count"], int)
        self.assertGreater(data["fix_issue"]["max_iteration_count"], 0)

    def test_partial_success_threshold_in_range(self):
        data = self.mod.load()
        t = data["fix_issue"]["partial_success_threshold"]
        self.assertGreaterEqual(t, 0)
        self.assertLessEqual(t, 1)

    def test_escalation_artifacts_values_valid(self):
        data = self.mod.load()
        for art in data["fix_issue"]["escalation_artifacts"]:
            self.assertIn(art, self.VALID_ESCALATION_ARTIFACTS,
                          f"unknown escalation artifact: {art}")


class RealConfigSanityTest(unittest.TestCase):
    """Sanity check the actual .pg/project.yaml in this repo.

    This catches drift between schema, config.yaml, and SKILL.md.
    """

    def test_real_config_yaml_passes_pg_fix_issue_filter(self):
        self.mod = load_parser()
        data = self.mod.load()
        filtered = self.mod.filter_by_workflow(data, "pg-fix-issue")

        self.assertIn("fix_issue", filtered,
                      "real .pg/project.yaml is missing fix_issue segment")

        fi = filtered["fix_issue"]
        self.assertEqual(fi["max_iteration_count"], 5)
        self.assertNotIn("max_per_iteration_subcalls", fi,
                         "max_per_iteration_subcalls removed in v3.x")
        self.assertEqual(fi["partial_success_threshold"], 0.7)
        if "escalation_artifacts" in fi:
            self.assertGreater(len(fi["escalation_artifacts"]), 0)

    def test_real_config_yaml_passes_all_workflow_filters(self):
        """Regression: ensure no workflow crashes when fix_issue is present."""
        self.mod = load_parser()
        data = self.mod.load()
        for wf in ("pg-build", "pg-propose", "pg-quick-build",
                   "pg-verify-and-merge", "pg-fix-issue"):
            filtered = self.mod.filter_by_workflow(data, wf)
            self.assertIsInstance(filtered, dict)
            self.assertIn("modules", filtered,
                          f"{wf} filter dropped modules segment")


if __name__ == "__main__":
    unittest.main(verbosity=2)
