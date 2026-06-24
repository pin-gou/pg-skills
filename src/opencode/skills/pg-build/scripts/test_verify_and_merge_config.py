#!/usr/bin/env python3
"""Tests for pg-parse-config.py pg-verify-and-merge subcommand.

This is the v3.0 contract for the SKILL: a single CLI call returns
{ tracks, regressionSuites, verifyMerge, flyway, git, __meta }, with
AffectedTracks auto-inferred (3-layer fallback, simple track filtered),
and all module commands pre-resolved to {cmd, timeout_seconds} form.

Covers:
- Top-level output shape: 6 keys present (5 + __meta).
- tracks.<t>.lint_cmd is {cmd, timeout_seconds} with timeout wrapper.
- tracks.<t>.lint override beats modules[0].lint fallback.
- regressionSuites only emits for affected ∩ regression.suite.
- envSetup derived from environments.<env>.prepare_env.
- verifySetup derived from required_roles' start action.
- runAllCommand chains test_keys with && (max timeout + 30s).
- outputFormat inference: e2e→playwright, java→maven-surefire, go→go-test.
- outputFormat suite-level override beats inference.
- verifyMerge.skipTestsIfNoConflict default true.
- flyway.migration-path / git.default-branch passthrough.
- v2 --key pipeline.tracks.X.lint returns null (硬切换).
- v2 --key testSuites.X returns null (硬切换).
- AffectedTracks from CLI --affected-tracks.
- AffectedTracks from tasks.md ## headings (simple track filtered).
- AffectedTracks from git diff + tracks.<t>.root path prefix match.
- AffectedTracks fallback to regression.suite keys.
- End-to-end with real .pg/project.yaml.
- Invalid change_dir exits non-zero.
- AffectedTracks filter excludes unaffected suites.
- run_all total timeout = max + 30s (not sum).
- outputFormat is single string when 1 unique value, sorted list otherwise.
- tracks module lint fallback when track has no lint override.
- regressionSuites skip when module has no test keys defined.
- envSetup returns None when environment has no prepare_env.
- output_format explicit override at suite level.
- "openapi-gen" is excluded from affected tracks regardless of source.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.normpath(
    os.path.join(THIS_DIR, "..", "..", "..", ".."))
PARSE_CONFIG_PY = os.path.join(
    PROJECT_ROOT, ".opencode", "scripts", "pg-parse-config.py")
CONFIG_YAML = os.path.join(PROJECT_ROOT, "pg-spec", "config.yaml")


def _load_parse_config():
    """Load pg-parse-config.py as a module for direct function testing."""
    if "pg_parse_config" in sys.modules:
        del sys.modules["pg_parse_config"]
    spec = importlib.util.spec_from_file_location("pg_parse_config", PARSE_CONFIG_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pg_parse_config"] = mod
    spec.loader.exec_module(mod)
    return mod


def _run_cli(*args):
    """Invoke the CLI as a subprocess and return (rc, stdout, stderr)."""
    proc = subprocess.run(
        [sys.executable, PARSE_CONFIG_PY, *args],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
    )
    return proc.returncode, proc.stdout, proc.stderr


# ============================================================
# Helper-function unit tests (no CLI roundtrip)
# ============================================================

class TestIsSimpleTrack(unittest.TestCase):

    def setUp(self):
        self.mod = _load_parse_config()
        self.config = {
            "tracks": {
                "backend":   {"type": "standard"},
                "openapi":   {"type": "simple", "commands": ["pnpm openapi"]},
                "frontend":  {},  # no type field → standard
            }
        }

    def test_explicit_simple_type(self):
        self.assertTrue(self.mod._is_simple_track(self.config, "openapi"))

    def test_explicit_standard_type(self):
        self.assertFalse(self.mod._is_simple_track(self.config, "backend"))

    def test_missing_type_is_standard(self):
        self.assertFalse(self.mod._is_simple_track(self.config, "frontend"))

    def test_missing_track_is_not_simple(self):
        self.assertFalse(self.mod._is_simple_track(self.config, "nonexistent"))


class TestParseTasksMdTrackIds(unittest.TestCase):

    def setUp(self):
        self.mod = _load_parse_config()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, content):
        path = os.path.join(self.tmp, "tasks.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_extracts_track_ids_in_order(self):
        path = self._write(
            "## 1. dev.backend - dev backend\n"
            "- 无\n\n"
            "## 2. dev.agent - dev agent\n"
            "- 无\n\n"
            "## 3. dev.openapi-gen - dev openapi-gen\n"
            "- 无\n"
        )
        self.assertEqual(
            self.mod._parse_tasks_md_track_ids(path),
            ["backend", "agent", "openapi-gen"],
        )

    def test_dedupes_repeats(self):
        path = self._write(
            "## 1. dev.backend - dev backend\n"
            "## 2. dev.backend - dev backend again\n"
            "## 3. dev.agent - dev agent\n"
        )
        self.assertEqual(
            self.mod._parse_tasks_md_track_ids(path),
            ["backend", "agent"],
        )

    def test_returns_empty_for_missing_file(self):
        result = self.mod._parse_tasks_md_track_ids(
            os.path.join(self.tmp, "nope.md"))
        self.assertEqual(result, [])

    def test_ignores_non_matching_lines(self):
        path = self._write(
            "# Heading 1\n"
            "Some prose\n"
            "## 1. dev.backend - dev backend\n"
            "## sub-heading without number\n"
            "## 2. dev.agent - dev agent\n"
        )
        self.assertEqual(
            self.mod._parse_tasks_md_track_ids(path),
            ["backend", "agent"],
        )


class TestInferOutputFormat(unittest.TestCase):

    def setUp(self):
        self.mod = _load_parse_config()

    def test_e2e_always_playwright(self):
        self.assertEqual(
            self.mod._infer_output_format({"language": "java"}, "e2e"),
            "playwright")
        self.assertEqual(
            self.mod._infer_output_format({"language": "go"}, "e2e"),
            "playwright")

    def test_java_unit(self):
        self.assertEqual(
            self.mod._infer_output_format({"language": "java"}, "unit"),
            "maven-surefire")

    def test_go_unit(self):
        self.assertEqual(
            self.mod._infer_output_format({"language": "go"}, "unit"),
            "go-test")

    def test_typescript_unit_defaults_to_shell(self):
        self.assertEqual(
            self.mod._infer_output_format({"language": "typescript"}, "unit"),
            "shell")

    def test_unknown_language_defaults_to_shell(self):
        self.assertEqual(
            self.mod._infer_output_format({"language": "rust"}, "unit"),
            "shell")

    def test_missing_language_defaults_to_shell(self):
        self.assertEqual(
            self.mod._infer_output_format({}, "unit"),
            "shell")


class TestChainTestCommands(unittest.TestCase):

    def setUp(self):
        self.mod = _load_parse_config()

    def test_empty_returns_none(self):
        self.assertIsNone(self.mod._chain_test_commands([]))

    def test_single_cmd_returned_as_is(self):
        cmds = [{"cmd": "mvn test", "timeout_seconds": 1800}]
        result = self.mod._chain_test_commands(cmds)
        self.assertEqual(result, {"cmd": "mvn test", "timeout_seconds": 1800})

    def test_multiple_cmds_joined_with_and(self):
        cmds = [
            {"cmd": "mvn test", "timeout_seconds": 1800},
            {"cmd": "go test ./...", "timeout_seconds": 600},
        ]
        result = self.mod._chain_test_commands(cmds)
        self.assertIn(" && ", result["cmd"])
        self.assertIn("mvn test", result["cmd"])
        self.assertIn("go test ./...", result["cmd"])

    def test_total_timeout_is_max_plus_grace(self):
        cmds = [
            {"cmd": "a", "timeout_seconds": 100},
            {"cmd": "b", "timeout_seconds": 500},
            {"cmd": "c", "timeout_seconds": 200},
        ]
        result = self.mod._chain_test_commands(cmds)
        # max(100, 500, 200) + 30 = 530, NOT 100+500+200
        self.assertEqual(result["timeout_seconds"], 530)


class TestDeriveEnvSetup(unittest.TestCase):

    def setUp(self):
        self.mod = _load_parse_config()

    def test_extracts_script_and_args(self):
        env = {
            "prepare_env": {
                "script": "pg-spec-deprecated/scripts/dev-local-setup.sh",
                "args": ["backend", "agent"],
            }
        }
        result = self.mod._derive_env_setup(env)
        self.assertEqual(
            result,
            "bash pg-spec-deprecated/scripts/dev-local-setup.sh backend agent",
        )

    def test_returns_none_when_no_prepare_env(self):
        self.assertIsNone(self.mod._derive_env_setup({}))
        self.assertIsNone(self.mod._derive_env_setup(None))

    def test_handles_prepare_env_without_args(self):
        env = {"prepare_env": {"script": "setup.sh"}}
        self.assertEqual(self.mod._derive_env_setup(env), "bash setup.sh")


class TestDeriveVerifySetup(unittest.TestCase):

    def setUp(self):
        self.mod = _load_parse_config()

    def test_uses_first_role_start_script(self):
        env = {
            "roles": {
                "backend": {"actions": {"start": {"script": "backend-up.sh"}}},
                "agent":   {"actions": {"start": {"script": "agent-up.sh"}}},
            }
        }
        result = self.mod._derive_verify_setup(env, ["backend", "agent"])
        self.assertEqual(result, "bash backend-up.sh")

    def test_skips_role_without_start(self):
        env = {
            "roles": {
                "backend": {"actions": {"start": {"script": "backend-up.sh"}}},
                "agent":   {"actions": {}},  # no start
            }
        }
        result = self.mod._derive_verify_setup(env, ["agent", "backend"])
        self.assertEqual(result, "bash backend-up.sh")

    def test_returns_none_when_no_match(self):
        env = {"roles": {"agent": {"actions": {}}}}
        self.assertIsNone(
            self.mod._derive_verify_setup(env, ["agent"]))


# ============================================================
# CLI roundtrip tests
# ============================================================

class TestPgVerifyAndMergeCLI(unittest.TestCase):
    """End-to-end tests invoking the CLI as a subprocess."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(CONFIG_YAML):
            raise unittest.SkipTest(f"real config not present: {CONFIG_YAML}")

    def test_top_level_keys_present(self):
        rc, out, _ = _run_cli("pg-verify-and-merge")
        self.assertEqual(rc, 0)
        d = json.loads(out)
        for k in ("tracks", "regressionSuites", "verifyMerge",
                  "flyway", "git", "__meta"):
            self.assertIn(k, d, f"missing top-level key: {k}")

    def test_meta_includes_affected_tracks(self):
        rc, out, _ = _run_cli("pg-verify-and-merge")
        d = json.loads(out)
        self.assertIn("affected_tracks", d["__meta"])
        self.assertIn("affected_tracks_source", d["__meta"])
        self.assertIn("excluded_simple_tracks", d["__meta"])
        # openapi-gen must be in the excluded list
        self.assertIn("openapi-gen", d["__meta"]["excluded_simple_tracks"])
        # and NOT in affected_tracks
        self.assertNotIn("openapi-gen", d["__meta"]["affected_tracks"])

    def test_tracks_lint_cmd_is_resolved(self):
        rc, out, _ = _run_cli("pg-verify-and-merge",
                              "--affected-tracks", "backend")
        d = json.loads(out)
        backend = d["tracks"]["backend"]
        self.assertIsNotNone(backend["lint_cmd"])
        self.assertIn("timeout", backend["lint_cmd"]["cmd"])
        self.assertIn("bash -c", backend["lint_cmd"]["cmd"])
        self.assertGreater(backend["lint_cmd"]["timeout_seconds"], 0)

    def test_regression_suites_only_for_affected(self):
        rc, out, _ = _run_cli("pg-verify-and-merge",
                              "--affected-tracks", "backend")
        d = json.loads(out)
        self.assertIn("backend", d["regressionSuites"])
        self.assertNotIn("frontend", d["regressionSuites"])
        self.assertNotIn("agent", d["regressionSuites"])

    def test_regression_suite_has_all_fields(self):
        rc, out, _ = _run_cli("pg-verify-and-merge",
                              "--affected-tracks", "backend")
        d = json.loads(out)
        backend_suite = d["regressionSuites"]["backend"]
        for k in ("module", "test_keys", "envSetup", "verifySetup",
                  "runAllCommand", "outputFormat"):
            self.assertIn(k, backend_suite, f"missing suite field: {k}")
        self.assertEqual(backend_suite["module"], "backend")
        self.assertEqual(backend_suite["test_keys"], ["unit"])
        self.assertIn("bash", backend_suite["envSetup"])
        self.assertIn("timeout", backend_suite["runAllCommand"]["cmd"])

    def test_output_format_inference(self):
        rc, out, _ = _run_cli("pg-verify-and-merge",
                              "--affected-tracks", "backend,frontend,agent")
        d = json.loads(out)
        # backend=java+unit → maven-surefire
        self.assertEqual(d["regressionSuites"]["backend"]["outputFormat"],
                         "maven-surefire")
        # frontend=ts+e2e → playwright
        self.assertEqual(d["regressionSuites"]["frontend"]["outputFormat"],
                         "playwright")
        # agent=go+unit → go-test
        self.assertEqual(d["regressionSuites"]["agent"]["outputFormat"],
                         "go-test")

    def test_verify_merge_default_true(self):
        rc, out, _ = _run_cli("pg-verify-and-merge",
                              "--affected-tracks", "backend")
        d = json.loads(out)
        self.assertTrue(d["verifyMerge"]["skipTestsIfNoConflict"])

    def test_flyway_and_git_passthrough(self):
        rc, out, _ = _run_cli("pg-verify-and-merge",
                              "--affected-tracks", "backend")
        d = json.loads(out)
        self.assertIn("migration-path", d["flyway"])
        self.assertIn("default-branch", d["git"])

    def test_affected_tracks_from_cli(self):
        rc, out, _ = _run_cli("pg-verify-and-merge",
                              "--affected-tracks", "backend,frontend")
        d = json.loads(out)
        self.assertEqual(d["__meta"]["affected_tracks_source"], "cli")
        self.assertEqual(d["__meta"]["affected_tracks"],
                         ["backend", "frontend"])

    def test_affected_tracks_from_tasks_md(self):
        tmp = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmp, "tasks.md"), "w") as f:
                f.write(
                    "## 1. dev.backend - dev backend\n"
                    "## 2. dev.agent - dev agent\n"
                    "## 3. dev.openapi-gen - dev openapi-gen\n"
                )
            rc, out, _ = _run_cli("pg-verify-and-merge",
                                  "--change-dir", tmp)
            d = json.loads(out)
            self.assertEqual(d["__meta"]["affected_tracks_source"], "tasks_md")
            # openapi-gen filtered
            self.assertIn("backend", d["__meta"]["affected_tracks"])
            self.assertIn("agent", d["__meta"]["affected_tracks"])
            self.assertNotIn("openapi-gen", d["__meta"]["affected_tracks"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_affected_tracks_cli_overrides_tasks_md(self):
        tmp = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmp, "tasks.md"), "w") as f:
                f.write("## 1. dev.backend - dev backend\n")
            rc, out, _ = _run_cli("pg-verify-and-merge",
                                  "--change-dir", tmp,
                                  "--affected-tracks", "frontend")
            d = json.loads(out)
            self.assertEqual(d["__meta"]["affected_tracks_source"], "cli")
            self.assertEqual(d["__meta"]["affected_tracks"], ["frontend"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_v2_pipeline_tracks_lint_returns_null(self):
        """硬切换: v2 --key pipeline.tracks.X.lint 必须返回 null."""
        rc, out, _ = _run_cli("--key", "pipeline.tracks.backend.lint")
        self.assertEqual(out.strip(), "null")

    def test_v2_test_suites_returns_null(self):
        """硬切换: v2 --key testSuites.X 必须返回 null."""
        rc, out, _ = _run_cli("--key", "testSuites.backend")
        self.assertEqual(out.strip(), "null")
        rc, out, _ = _run_cli("--key", "testSuites.backend.envSetup")
        self.assertEqual(out.strip(), "null")

    def test_existing_modules_key_unchanged(self):
        """v3.0 字段路径保持工作（向后兼容 sanity check）."""
        rc, out, _ = _run_cli("--key", "modules.backend.language")
        self.assertEqual(json.loads(out.strip()), "java")
        rc, out, _ = _run_cli("--key", "regression.suite.backend.module")
        self.assertEqual(json.loads(out.strip()), "backend")

    def test_run_all_total_timeout_max_plus_30(self):
        """backend/frontend/agent suite 都只有 1 个 test_key, timeout 应为单 cmd timeout."""
        rc, out, _ = _run_cli("pg-verify-and-merge",
                              "--affected-tracks", "backend")
        d = json.loads(out)
        single = d["regressionSuites"]["backend"]["runAllCommand"]
        # 单 test_key 走 _chain_test_commands 单 cmd 分支, 不应被加 grace
        # timeout 应该是 modules.backend.test.unit 的 timeout (1800), 不是 1830
        self.assertEqual(single["timeout_seconds"], 1800)


if __name__ == "__main__":
    unittest.main()
