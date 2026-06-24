#!/usr/bin/env python3
"""Tests for prompt_injection assembly in pg-pipeline-runner.py.

Verifies that _enrich_context_with_prompt_injection:
  - Selects rules whose target_agent matches pg-build/{sub}
  - Routes position=prepend to prepend, default/append to append
  - Concatenates multiple rules in config order, separated by \\n\\n
  - Skips non-inject-prompt types and target mismatches
  - Skips rules with empty templates
  - Tracks applied rule ids in rules_applied
  - dispatch_action embeds the assembled prompt_injection in the top-level
    dispatch JSON
"""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = next(p for p in Path(__file__).resolve().parents if (p / ".pg" / "project.yaml").exists())
RUNNER_DIR = PROJECT_ROOT / ".opencode" / "skills" / "pg-build" / "scripts"
SCRIPT_PATH = RUNNER_DIR / "pg-pipeline-runner.py"


def load_runner():
    sys.path.insert(0, str(RUNNER_DIR))
    spec = importlib.util.spec_from_file_location(
        "pg_pipeline_runner", str(SCRIPT_PATH)
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SAMPLE_CONFIG = """\
schema: spec-driven

pipeline:
  order: [backend]
  tracks:
    backend:
      type: track
      label: "Backend"
      root: <module-name>

apply_change_rules:
  - id: dev_check
    type: inject-prompt
    target_agent: pg-build/dev
    position: prepend
    template: "[CAPABILITY_CHECKLIST]"
  - id: verify_check
    type: inject-prompt
    target_agent: pg-build/verify
    position: prepend
    template: "[CAPABILITY_VERIFY_STEP]"
  - id: dev_append_extra
    type: inject-prompt
    target_agent: pg-build/dev
    position: append
    template: "[EXTRA-APPEND]"
  - id: wrong_type
    type: run-script
    target_agent: pg-build/dev
    position: prepend
    template: "[SHOULD-BE-SKIPPED]"
  - id: no_template
    type: inject-prompt
    target_agent: pg-build/dev
    position: prepend
    template: ""
"""


class PromptInjectionAssemblyTest(unittest.TestCase):

    def setUp(self):
        self.runner = load_runner()
        fd, self.tmp_path = tempfile.mkstemp(suffix=".yaml", text=True)
        os.write(fd, SAMPLE_CONFIG.encode("utf-8"))
        os.close(fd)
        self.config = self.runner.load_config.__wrapped__ if hasattr(
            self.runner.load_config, "__wrapped__"
        ) else self.runner.load_config
        # runner's load_config reads CONFIG_PATH module-globally; we read
        # the file via pyyaml directly and pass the dict forward.
        import yaml
        with open(self.tmp_path, encoding="utf-8") as f:
            self.config_data = yaml.safe_load(f)

    def tearDown(self):
        os.unlink(self.tmp_path)

    def _inject(self, sub):
        ctx: dict = {}
        self.runner._enrich_context_with_prompt_injection(
            ctx, self.config_data, "backend", sub
        )
        return ctx["prompt_injection"]

    def test_dev_picks_only_dev_rules(self):
        pi = self._inject("dev")
        self.assertEqual(pi["target_agent"], "pg-build/dev")
        self.assertIn("dev_check", pi["rules_applied"])
        self.assertIn("dev_append_extra", pi["rules_applied"])
        self.assertNotIn("verify_check", pi["rules_applied"])
        self.assertNotIn("wrong_type", pi["rules_applied"])
        self.assertNotIn("no_template", pi["rules_applied"])

    def test_verify_picks_only_verify_rules(self):
        pi = self._inject("verify")
        self.assertEqual(pi["target_agent"], "pg-build/verify")
        self.assertEqual(pi["rules_applied"], ["verify_check"])
        self.assertEqual(pi["prepend"], "[CAPABILITY_VERIFY_STEP]")

    def test_prepend_and_append_routing(self):
        pi = self._inject("dev")
        self.assertEqual(pi["prepend"], "[CAPABILITY_CHECKLIST]")
        self.assertEqual(pi["append"], "[EXTRA-APPEND]")

    def test_multiple_prepend_concatenated_in_order(self):
        import yaml
        cfg = yaml.safe_load(SAMPLE_CONFIG + """
  - id: dev_extra_prepend
    type: inject-prompt
    target_agent: pg-build/dev
    position: prepend
    template: "[B]"
""")
        ctx: dict = {}
        self.runner._enrich_context_with_prompt_injection(ctx, cfg, "backend", "dev")
        self.assertEqual(ctx["prompt_injection"]["prepend"],
                         "[CAPABILITY_CHECKLIST]\n\n[B]")

    def test_empty_prepend_and_append_when_no_rules(self):
        import yaml
        cfg = yaml.safe_load("schema: spec-driven\napply_change_rules: []\n")
        ctx: dict = {}
        self.runner._enrich_context_with_prompt_injection(ctx, cfg, "backend", "dev")
        self.assertEqual(ctx["prompt_injection"]["prepend"], "")
        self.assertEqual(ctx["prompt_injection"]["append"], "")
        self.assertEqual(ctx["prompt_injection"]["rules_applied"], [])

    def test_dispatch_action_embeds_prompt_injection(self):
        ctx = {"prompt_injection": {
            "target_agent": "pg-build/dev",
            "prepend": "[X]",
            "append": "[Y]",
            "rules_applied": ["dev_check"],
        }}
        action = self.runner.dispatch_action(
            agent="pg-build/dev",
            item="backend",
            sub="dev",
            context=ctx,
            attempt=1,
        )
        self.assertIn("prompt_injection", action)
        self.assertEqual(action["prompt_injection"]["prepend"], "[X]")
        self.assertEqual(action["prompt_injection"]["append"], "[Y]")
        self.assertEqual(action["prompt_injection"]["rules_applied"], ["dev_check"])

    def test_dispatch_action_default_when_no_prompt_injection_in_ctx(self):
        action = self.runner.dispatch_action(
            agent="pg-build/dev",
            item="backend",
            sub="dev",
            context={},
            attempt=1,
        )
        pi = action["prompt_injection"]
        self.assertEqual(pi["target_agent"], "pg-build/dev")
        self.assertEqual(pi["prepend"], "")
        self.assertEqual(pi["append"], "")
        self.assertEqual(pi["rules_applied"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
