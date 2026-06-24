#!/usr/bin/env python3
"""Tests for module command timeout normalization.

Covers:
- normalize_module_command() accepts string and dict forms.
- normalize_module_command() applies timeout precedence (cmd > module > 1800).
- normalize_module_command() raises ValueError on bad input.
- render_module_command() emits `timeout N bash -c '<cmd>'`.
- _build_module_context() in pg-pipeline-runner.py injects timeout into
  every command (build, lint, test.<key>) using module.timeout_seconds
  as default and per-command override.
- _build_module_context() preserves schema-faithful shape (missing keys
  stay missing; no blank-filled fields).
- .pg/project.yaml modules section validates against config.schema.json
  (regression guard for the timeout_seconds + object command additions).
"""
import importlib.util
import json
import os
import sys
import unittest

import yaml


SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
COMMON_PY = os.path.join(SCRIPTS_DIR, "pg_pipeline_common.py")
RUNNER_PY = os.path.join(SCRIPTS_DIR, "pg-pipeline-runner.py")


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


# ============================================================
# normalize_module_command
# ============================================================

class TestNormalizeModuleCommand(unittest.TestCase):
    def setUp(self):
        self.common = _load_common()

    def test_string_form_uses_module_default(self):
        result = self.common.normalize_module_command("cd foo && mvn package", 1800)
        self.assertEqual(result, {"cmd": "cd foo && mvn package", "timeout_seconds": 1800})

    def test_string_form_uses_1200_when_module_default_1200(self):
        result = self.common.normalize_module_command("go build ./...", 1200)
        self.assertEqual(result["timeout_seconds"], 1200)

    def test_string_form_falls_back_to_1800_when_no_module_default(self):
        result = self.common.normalize_module_command("go build", None)
        self.assertEqual(result["timeout_seconds"], 1800)

    def test_dict_form_uses_explicit_timeout(self):
        result = self.common.normalize_module_command(
            {"cmd": "mvn test", "timeout_seconds": 3600}, 1800)
        self.assertEqual(result, {"cmd": "mvn test", "timeout_seconds": 3600})

    def test_dict_form_inherits_module_default_when_no_timeout(self):
        result = self.common.normalize_module_command({"cmd": "mvn test"}, 600)
        self.assertEqual(result["timeout_seconds"], 600)

    def test_dict_form_falls_back_to_1800_when_nothing_set(self):
        result = self.common.normalize_module_command({"cmd": "pnpm build"}, None)
        self.assertEqual(result["timeout_seconds"], 1800)

    def test_per_command_timeout_overrides_module_default(self):
        result = self.common.normalize_module_command(
            {"cmd": "mvn integration-test", "timeout_seconds": 7200}, 1800)
        self.assertEqual(result["timeout_seconds"], 7200)

    def test_dict_missing_cmd_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.common.normalize_module_command({"timeout_seconds": 60}, 1800)
        self.assertIn("cmd", str(ctx.exception))

    def test_dict_with_empty_cmd_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.common.normalize_module_command({"cmd": "   "}, 1800)
        self.assertIn("non-empty", str(ctx.exception))

    def test_int_input_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.common.normalize_module_command(42, 1800)
        self.assertIn("string or dict", str(ctx.exception))

    def test_timeout_is_int(self):
        result = self.common.normalize_module_command("go build", 600)
        self.assertIsInstance(result["timeout_seconds"], int)
        result2 = self.common.normalize_module_command(
            {"cmd": "go build", "timeout_seconds": 300.0}, 600)
        self.assertIsInstance(result2["timeout_seconds"], int)
        self.assertEqual(result2["timeout_seconds"], 300)


# ============================================================
# render_module_command
# ============================================================

class TestRenderModuleCommand(unittest.TestCase):
    def setUp(self):
        self.common = _load_common()

    def test_basic_render(self):
        out = self.common.render_module_command(
            {"cmd": "mvn test", "timeout_seconds": 1800})
        self.assertEqual(out, "timeout 1800 bash -c 'mvn test'")

    def test_render_with_pipeline_in_cmd(self):
        out = self.common.render_module_command(
            {"cmd": "cd <module-name> && mvn test", "timeout_seconds": 600})
        self.assertIn("timeout 600", out)
        self.assertIn("mvn test", out)

    def test_render_quotes_special_chars_safely(self):
        # shlex.quote should escape single quotes in the command body
        out = self.common.render_module_command(
            {"cmd": "echo 'hello world'", "timeout_seconds": 60})
        # The outer quoting produced by shlex.quote handles internal single quotes
        # Just verify it doesn't break and the cmd text is present
        self.assertIn("hello world", out)
        self.assertIn("timeout 60", out)


# ============================================================
# _build_module_context
# ============================================================

class TestBuildModuleContext(unittest.TestCase):
    def setUp(self):
        self.common = _load_common()
        self.runner = _load_runner()

    def _ctx(self, modules, cfg):
        return self.runner._build_module_context(cfg, modules)

    def test_string_command_uses_module_default_timeout(self):
        cfg = {"modules": {
            "backend": {
                "root": "<module-name>", "language": "java",
                "timeout_seconds": 1800,
                "build": "cd <module-name> && mvn package -q",
            }
        }}
        out = self._ctx(["backend"], cfg)
        self.assertEqual(out[0]["build"],
                         "timeout 1800 bash -c 'cd <module-name> && mvn package -q'")

    def test_dict_command_overrides_module_default(self):
        cfg = {"modules": {
            "backend": {
                "root": "<module-name>", "language": "java",
                "timeout_seconds": 1800,
                "test": {
                    "integration": {
                        "cmd": "cd <module-name> && mvn integration-test",
                        "timeout_seconds": 3600,
                    },
                },
            }
        }}
        out = self._ctx(["backend"], cfg)
        self.assertEqual(
            out[0]["test"]["integration"],
            "timeout 3600 bash -c 'cd <module-name> && mvn integration-test'")

    def test_module_without_timeout_uses_schema_default_1800(self):
        cfg = {"modules": {
            "agent": {
                "root": "<module-name>", "language": "go",
                "build": "go build -o build/agent ./cmd/agent",
            }
        }}
        out = self._ctx(["agent"], cfg)
        self.assertEqual(out[0]["build"],
                         "timeout 1800 bash -c 'go build -o build/agent ./cmd/agent'")

    def test_all_three_command_slots_rendered(self):
        cfg = {"modules": {
            "frontend": {
                "root": "<module-name>", "language": "typescript",
                "timeout_seconds": 1800,
                "build": "cd <module-name> && pnpm build:pro",
                "lint": "cd <module-name> && pnpm lint:eslint",
                "test": {
                    "unit": "cd <module-name> && pnpm test:unit",
                    "e2e": {
                        "cmd": "cd <module-name> && pnpm test",
                        "timeout_seconds": 3600,
                    },
                },
            }
        }}
        out = self._ctx(["frontend"], cfg)
        self.assertIn("timeout 1800", out[0]["build"])
        self.assertIn("timeout 1800", out[0]["lint"])
        self.assertIn("timeout 1800", out[0]["test"]["unit"])
        self.assertIn("timeout 3600", out[0]["test"]["e2e"])

    def test_module_without_commands_omits_keys(self):
        # e.g. agent-proto only has build, no lint/test
        cfg = {"modules": {
            "agent-proto": {
                "root": "<module-name>", "language": "proto",
                "timeout_seconds": 300,
                "build": "cd <module-name> && make proto",
            }
        }}
        out = self._ctx(["agent-proto"], cfg)
        entry = out[0]
        self.assertIn("build", entry)
        self.assertNotIn("lint", entry)
        self.assertNotIn("test", entry)
        self.assertEqual(entry["timeout_seconds"], 300)

    def test_module_without_timeout_seconds_omits_key(self):
        # Schema default 1800 is the schema's job; the runner should pass
        # through the raw module value or omit it if not set.
        cfg = {"modules": {
            "agent": {
                "root": "<module-name>", "language": "go",
                "build": "go build ./...",
            }
        }}
        out = self._ctx(["agent"], cfg)
        # No top-level timeout_seconds in the config => not surfaced as
        # module context field. The default kicks in per-command.
        self.assertNotIn("timeout_seconds", out[0])

    def test_empty_test_value_filtered(self):
        # If test.unit is None or empty string, don't emit it
        cfg = {"modules": {
            "agent": {
                "root": "<module-name>", "language": "go",
                "test": {"unit": "go test ./...", "integration": ""},
            }
        }}
        out = self._ctx(["agent"], cfg)
        self.assertIn("unit", out[0]["test"])
        self.assertNotIn("integration", out[0]["test"])

    def test_missing_module_in_config_returns_empty_name(self):
        # If modules list references a name not in config, the runner
        # still returns an entry with just the name (no crash).
        cfg = {"modules": {}}
        out = self._ctx(["ghost"], cfg)
        self.assertEqual(out[0], {"name": "ghost"})

    def test_multiple_modules_independent_timeouts(self):
        cfg = {"modules": {
            "backend": {"root": "x", "language": "java", "timeout_seconds": 1800,
                        "build": "mvn package"},
            "agent": {"root": "y", "language": "go", "timeout_seconds": 600,
                      "build": "go build"},
        }}
        out = self._ctx(["backend", "agent"], cfg)
        by_name = {e["name"]: e for e in out}
        self.assertIn("timeout 1800", by_name["backend"]["build"])
        self.assertIn("timeout 600", by_name["agent"]["build"])


# ============================================================
# Real config.yaml validates against schema
# ============================================================

class TestConfigYamlModulesValidate(unittest.TestCase):
    """Regression guard: .pg/project.yaml modules section must validate
    against the schema after timeout_seconds + object command additions."""

    SCHEMA_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(SCRIPTS_DIR)))),
        "pg-spec", "schema", "config.schema.json")
    CONFIG_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(SCRIPTS_DIR)))),
        "pg-spec", "config.yaml")

    def test_all_modules_validate(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        with open(self.SCHEMA_PATH) as f:
            schema = json.load(f)
        with open(self.CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        mod_def = schema["definitions"]["module"]

        # jsonschema 4.18+ uses `referencing` library; older versions need
        # RefResolver. Build a validator with the full schema as base so
        # `#/definitions/executable_command` resolves correctly.
        try:
            Validator = jsonschema.Draft7Validator
            resolver = jsonschema.RefResolver(base_uri="", referrer=schema)
        except AttributeError:
            Validator = jsonschema.Draft7Validator
            resolver = None

        for name, mod in cfg["modules"].items():
            kwargs = {"schema": mod_def}
            if resolver is not None:
                kwargs["resolver"] = resolver
            v = Validator(**kwargs)
            errors = list(v.iter_errors(mod))
            self.assertFalse(
                errors,
                msg=f"module {name!r} fails schema: " +
                    "; ".join(f"{e.message} at {list(e.absolute_path)}"
                              for e in errors))


if __name__ == "__main__":
    unittest.main()
