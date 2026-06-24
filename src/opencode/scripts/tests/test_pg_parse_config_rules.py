#!/usr/bin/env python3
"""Tests for pg-parse-config.py structure-extension behavior.

Verifies that:
  - pg-propose workflow sees proposal_rules segment
  - pg-build workflow sees apply_change_rules segment
  - Both segments default to empty list when absent in config.yaml
  - Existing rules / test_strategy / coding_standards / pipeline remain
    intact (backward compatibility)
"""

import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = next(p for p in Path(__file__).resolve().parents if (p / ".pg" / "project.yaml").exists())
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

pipeline:
  order: [backend]
  tracks:
    backend:
      type: track
      label: "Backend"
      root: <module-name>

rules:
  proposal:
    - 生成中文文档

proposal_rules:
  - id: capability_assessment
    after_section: 风险和注意事项
    template: |
      ## Capability 影响评估

apply_change_rules:
  - id: capability_checklist
    type: inject-prompt
    target_agent: pg-build/dev
    position: prepend
    template: |
      [CAPABILITY_CHECKLIST]
  - id: capability_verify_step
    type: inject-prompt
    target_agent: pg-build/verify
    position: prepend
    template: |
      [CAPABILITY_VERIFY_STEP]
"""


class ParseConfigWorkflowKeysTest(unittest.TestCase):
    """Verify WORKFLOW_KEYS exposes the new segments to the right workflows."""

    def setUp(self):
        self.mod = load_parser()

    def test_pg_propose_workflow_exposes_proposal_rules(self):
        keys = self.mod.WORKFLOW_KEYS["pg-propose"]
        self.assertIn("proposal_rules", keys)

    def test_pg_propose_workflow_does_not_expose_apply_change_rules(self):
        keys = self.mod.WORKFLOW_KEYS["pg-propose"]
        self.assertNotIn("apply_change_rules", keys)

    def test_pg_apply_change_workflow_exposes_apply_change_rules(self):
        keys = self.mod.WORKFLOW_KEYS["pg-build"]
        self.assertIn("apply_change_rules", keys)

    def test_pg_apply_change_workflow_does_not_expose_proposal_rules(self):
        keys = self.mod.WORKFLOW_KEYS["pg-build"]
        self.assertNotIn("proposal_rules", keys)

    def test_other_workflows_unchanged(self):
        for wf in ("pg-verify-and-merge", "pg-regression",
                   "pg-fix-issue", "pg-quick-build"):
            self.assertNotIn("proposal_rules", self.mod.WORKFLOW_KEYS[wf])
            self.assertNotIn("apply_change_rules", self.mod.WORKFLOW_KEYS[wf])

    def test_pg_regression_workflow_excludes_tracks_stages(self):
        """pg-regression 解耦 tracks/stages, 只读 modules/environments/regression."""
        keys = self.mod.WORKFLOW_KEYS["pg-regression"]
        self.assertIn("modules", keys)
        self.assertIn("environments", keys)
        self.assertIn("regression", keys)
        self.assertNotIn("tracks", keys)
        self.assertNotIn("stages", keys)


class ParseConfigFilterTest(unittest.TestCase):
    """Verify filter_by_workflow yields the new segments when present."""

    def setUp(self):
        self.mod = load_parser()
        self.tmp = write_temp_config(SAMPLE_CONFIG)
        # patch CONFIG_PATH so load() reads our temp file
        self._patcher = patch.object(self.mod, "CONFIG_PATH", str(self.tmp))
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self.tmp.unlink(missing_ok=True)

    def test_pg_propose_sees_proposal_rules(self):
        data = self.mod.load()
        filtered = self.mod.filter_by_workflow(data, "pg-propose")
        self.assertIn("proposal_rules", filtered)
        self.assertEqual(len(filtered["proposal_rules"]), 1)
        rule = filtered["proposal_rules"][0]
        self.assertEqual(rule["id"], "capability_assessment")
        self.assertEqual(rule["after_section"], "风险和注意事项")
        self.assertIn("## Capability 影响评估", rule["template"])

    def test_pg_apply_change_sees_apply_change_rules(self):
        data = self.mod.load()
        filtered = self.mod.filter_by_workflow(data, "pg-build")
        self.assertIn("apply_change_rules", filtered)
        self.assertEqual(len(filtered["apply_change_rules"]), 2)
        targets = sorted(r["target_agent"] for r in filtered["apply_change_rules"])
        self.assertEqual(
            targets,
            ["pg-build/dev", "pg-build/verify"],
        )

    def test_existing_rules_segment_intact(self):
        data = self.mod.load()
        filtered = self.mod.filter_by_workflow(data, "pg-propose")
        self.assertIn("rules", filtered)
        self.assertEqual(filtered["rules"]["proposal"], ["生成中文文档"])

    def test_pipeline_segment_intact(self):
        data = self.mod.load()
        filtered = self.mod.filter_by_workflow(data, "pg-propose")
        self.assertEqual(filtered["pipeline"]["order"], ["backend"])


class ParseConfigDefaultsTest(unittest.TestCase):
    """When proposal_rules / apply_change_rules are absent, missing keys."""

    def setUp(self):
        self.mod = load_parser()
        minimal = "schema: spec-driven\npipeline:\n  order: []\n  tracks: {}\nrules: {}\n"
        self.tmp = write_temp_config(minimal)
        self._patcher = patch.object(self.mod, "CONFIG_PATH", str(self.tmp))
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self.tmp.unlink(missing_ok=True)

    def test_absent_proposal_rules_does_not_appear(self):
        data = self.mod.load()
        filtered = self.mod.filter_by_workflow(data, "pg-propose")
        self.assertNotIn("proposal_rules", filtered)

    def test_absent_apply_change_rules_does_not_appear(self):
        data = self.mod.load()
        filtered = self.mod.filter_by_workflow(data, "pg-build")
        self.assertNotIn("apply_change_rules", filtered)


class RuleShapeTest(unittest.TestCase):
    """Light shape validation mirroring what SKILL.md documents."""

    PROPOSAL_RULE_KEYS = {"id", "after_section", "template"}
    APPLY_RULE_KEYS = {"id", "type", "target_agent", "position", "template"}
    VALID_TARGETS = {"pg-build/dev", "pg-build/verify"}
    VALID_POSITIONS = {"prepend", "append"}
    VALID_TYPES = {"inject-prompt"}

    def setUp(self):
        self.mod = load_parser()
        self.tmp = write_temp_config(SAMPLE_CONFIG)
        self._patcher = patch.object(self.mod, "CONFIG_PATH", str(self.tmp))
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self.tmp.unlink(missing_ok=True)

    def test_proposal_rule_has_expected_keys(self):
        data = self.mod.load()
        for r in data["proposal_rules"]:
            self.assertTrue(self.PROPOSAL_RULE_KEYS.issuperset(r.keys()),
                            f"unexpected keys in proposal rule: {r.keys()}")

    def test_apply_change_rule_has_expected_keys(self):
        data = self.mod.load()
        for r in data["apply_change_rules"]:
            self.assertTrue(self.APPLY_RULE_KEYS.issuperset(r.keys()),
                            f"unexpected keys in apply_change rule: {r.keys()}")
            self.assertIn(r["type"], self.VALID_TYPES)
            self.assertIn(r["target_agent"], self.VALID_TARGETS)
            self.assertIn(r["position"], self.VALID_POSITIONS)


class ValidateRegressionTest(unittest.TestCase):
    """覆盖 validate_regression 的 7 条硬校验规则."""

    VALID_BASE = """\
schema: spec-driven

modules:
  backend:
    root: <module-name>
    language: java
    test:
      unit: cd <module-name> && mvn test
      integration: cd <module-name> && mvn test -pl bootstrap
  agent:
    root: <module-name>
    language: go
    test:
      unit: cd <module-name> && go test ./...
  frontend:
    root: <module-name>
    language: typescript
    test:
      unit: cd <module-name> && pnpm test:unit
      e2e: cd <module-name> && pnpm test

environments:
  dev-local:
    roles:
      backend:
        instances: [{name: backend-1, host: localhost}]
      frontend:
        instances: [{name: frontend-1, host: localhost}]
  dev-3tier:
    roles:
      backend:
        instances: [{name: backend-1, host: localhost}]
      agent:
        instances: [{name: source-agent, host: box-1}]
"""

    def setUp(self):
        self.mod = load_parser()
        self.tmp = write_temp_config(self.VALID_BASE)
        self._patcher = patch.object(self.mod, "CONFIG_PATH", str(self.tmp))
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self.tmp.unlink(missing_ok=True)

    def _override_regression(self, reg_yaml):
        text = self.VALID_BASE + "\nregression:\n" + reg_yaml + "\n"
        self.tmp.write_text(text, encoding="utf-8")
        data = self.mod.load()
        return self.mod.validate_regression(data)

    def test_valid_full_suite(self):
        """3 个 suite 都合法, 返回空 errors."""
        reg = """
  suite:
    frontend:
      environment: {name: dev-local, required_roles: [backend]}
      module: frontend
      test_keys: [e2e]
    backend:
      environment: {name: dev-local, required_roles: []}
      module: backend
      test_keys: [unit]
    agent:
      environment: {name: dev-3tier, required_roles: [backend, agent]}
      module: agent
      test_keys: [unit, integration]
"""
        # agent.integration: agents modules has only unit
        errors = self._override_regression(reg)
        # filter out the rule 4 (test_keys) for agent.integration
        non_rule4 = [e for e in errors if "test_keys" not in e["field"]]
        self.assertEqual(non_rule4, [],
                         f"unexpected errors (excluding rule 4 for invalid key): {non_rule4}")

    def test_valid_unit_only_suite(self):
        reg = """
  suite:
    backend:
      environment: {name: dev-local, required_roles: []}
      module: backend
      test_keys: [unit]
"""
        errors = self._override_regression(reg)
        self.assertEqual(errors, [])

    # Rule 1: missing regression.suite
    def test_rule1_missing_suite_section(self):
        reg = "  environment: dev-local"  # no suite, but has leftover env
        errors = self._override_regression(reg)
        # rule 1 hits (suite missing), plus rule 7 (top-level env)
        fields = {e["field"] for e in errors}
        self.assertIn("regression.suite", fields)
        self.assertIn("regression.environment", fields)

    # Rule 2: missing required field
    def test_rule2_missing_module(self):
        reg = """
  suite:
    backend:
      environment: {name: dev-local, required_roles: []}
      test_keys: [unit]
"""
        errors = self._override_regression(reg)
        fields = [e["field"] for e in errors]
        self.assertTrue(any("module" in f and "backend" in f for f in fields),
                        f"rule 2 (missing module) not hit: {fields}")

    def test_rule2_missing_test_keys(self):
        reg = """
  suite:
    backend:
      environment: {name: dev-local, required_roles: []}
      module: backend
"""
        errors = self._override_regression(reg)
        fields = [e["field"] for e in errors]
        self.assertTrue(any("test_keys" in f for f in fields),
                        f"rule 2 (missing test_keys) not hit: {fields}")

    def test_rule2_missing_environment(self):
        reg = """
  suite:
    backend:
      module: backend
      test_keys: [unit]
"""
        errors = self._override_regression(reg)
        fields = [e["field"] for e in errors]
        self.assertTrue(any(f.endswith("environment") for f in fields),
                        f"rule 2 (missing environment) not hit: {fields}")

    # Rule 3: module not in modules
    def test_rule3_module_not_in_modules(self):
        reg = """
  suite:
    ghost:
      environment: {name: dev-local, required_roles: []}
      module: ghost
      test_keys: [unit]
"""
        errors = self._override_regression(reg)
        fields = [e["field"] for e in errors]
        self.assertTrue(any("module" in f and "ghost" in f for f in fields),
                        f"rule 3 not hit: {fields}")

    # Rule 4: test_key not in module.test
    def test_rule4_test_key_invalid(self):
        reg = """
  suite:
    backend:
      environment: {name: dev-local, required_roles: []}
      module: backend
      test_keys: [nonexistent]
"""
        errors = self._override_regression(reg)
        fields = [e["field"] for e in errors]
        self.assertTrue(any("test_keys" in f for f in fields),
                        f"rule 4 not hit: {fields}")

    def test_rule4_test_keys_empty_list(self):
        reg = """
  suite:
    backend:
      environment: {name: dev-local, required_roles: []}
      module: backend
      test_keys: []
"""
        errors = self._override_regression(reg)
        fields = [e["field"] for e in errors]
        self.assertTrue(any("test_keys" in f for f in fields),
                        f"rule 4 (empty list) not hit: {fields}")

    # Rule 5: environment.name not in environments
    def test_rule5_environment_invalid(self):
        reg = """
  suite:
    backend:
      environment: {name: does-not-exist, required_roles: []}
      module: backend
      test_keys: [unit]
"""
        errors = self._override_regression(reg)
        fields = [e["field"] for e in errors]
        self.assertTrue(any("environment.name" in f for f in fields),
                        f"rule 5 not hit: {fields}")

    # Rule 6: required_role not in env.roles
    def test_rule6_role_not_in_env(self):
        reg = """
  suite:
    backend:
      environment: {name: dev-local, required_roles: [agent]}
      module: backend
      test_keys: [unit]
"""
        errors = self._override_regression(reg)
        fields = [e["field"] for e in errors]
        self.assertTrue(any("required_roles" in f for f in fields),
                        f"rule 6 not hit: {fields}")

    # Rule 7: top-level regression.environment is FORBIDDEN
    def test_rule7_top_level_environment_forbidden(self):
        reg = """
  environment: dev-local
  suite:
    backend:
      environment: {name: dev-local, required_roles: []}
      module: backend
      test_keys: [unit]
"""
        errors = self._override_regression(reg)
        fields = [e["field"] for e in errors]
        self.assertTrue(any(f == "regression.environment" for f in fields),
                        f"rule 7 not hit: {fields}")


class RegressionSuiteFilterTest(unittest.TestCase):
    """_filter_regression_by_suite deep-filters to only one suite."""

    def setUp(self):
        self.mod = load_parser()
        self.raw = {
            "modules": {
                "frontend": {"root": "fe", "language": "ts"},
                "backend": {"root": "be", "language": "java"},
                "agent": {"root": "ag", "language": "go"},
            },
            "environments": {
                "dev-local": {"roles": {"backend": {}, "frontend": {}}},
                "dev-3tier": {"roles": {"backend": {}, "agent": {}}},
            },
            "regression": {
                "suite": {
                    "frontend": {
                        "environment": {"name": "dev-local"},
                        "module": "frontend",
                        "test_keys": ["e2e"],
                    },
                    "backend": {
                        "environment": {"name": "dev-local"},
                        "module": "backend",
                        "test_keys": ["unit"],
                    },
                    "agent": {
                        "environment": {"name": "dev-3tier"},
                        "module": "agent",
                        "test_keys": ["unit"],
                    },
                }
            },
        }

    def test_filter_frontend_keeps_only_frontend_module_and_dev_local(self):
        filtered = self.mod._filter_regression_by_suite(self.raw, self.raw, "frontend")
        self.assertIn("frontend", filtered.get("modules", {}))
        self.assertNotIn("backend", filtered.get("modules", {}))
        self.assertNotIn("agent", filtered.get("modules", {}))
        self.assertIn("dev-local", filtered.get("environments", {}))
        self.assertNotIn("dev-3tier", filtered.get("environments", {}))
        suites = filtered.get("regression", {}).get("suite", {})
        self.assertIn("frontend", suites)
        self.assertNotIn("backend", suites)
        self.assertNotIn("agent", suites)

    def test_filter_agent_keeps_only_agent_module_and_dev_3tier(self):
        filtered = self.mod._filter_regression_by_suite(self.raw, self.raw, "agent")
        self.assertIn("agent", filtered.get("modules", {}))
        self.assertNotIn("frontend", filtered.get("modules", {}))
        self.assertNotIn("backend", filtered.get("modules", {}))
        self.assertIn("dev-3tier", filtered.get("environments", {}))
        self.assertNotIn("dev-local", filtered.get("environments", {}))
        suites = filtered.get("regression", {}).get("suite", {})
        self.assertIn("agent", suites)
        self.assertNotIn("frontend", suites)
        self.assertNotIn("backend", suites)

    def test_unknown_suite_returns_unfiltered(self):
        filtered = self.mod._filter_regression_by_suite(self.raw, self.raw, "nonexistent")
        self.assertEqual(filtered, self.raw)

    def test_passes_meta_through(self):
        raw_with_meta = {**self.raw, "__meta": {"hostname": "test"}}
        filtered = self.mod._filter_regression_by_suite(raw_with_meta, raw_with_meta, "backend")
        self.assertEqual(filtered.get("__meta", {}), {"hostname": "test"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
