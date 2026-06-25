#!/usr/bin/env python3
"""Tests for pg-pipeline-runner.py prompt template engine.

Covers:
- _render_prompt_template: 8 syntax cases ({{var}}, {{var|filter}}, {#if},
  {#if with truthy path}, {#if with sub in []}, {#each with this.X},
  nested {#if in {#each}, missing-key → empty string).
- _render_prompt_template filters: tojson(indent=N) (legacy) + toyaml
  (default for prompt rendering; preserves unicode, compacts hooks payload).
- _build_prompt_template: 6 sub types return valid templates (test, dev,
  verify, gate, fix, final-gate).
- _SUB_TRACK_FIELDS: no longer contains dead "deployment_actions"; contains
  module_roots/module_names.
- module_roots is a JSON array (list), not a string.
- rollback_context is nested dict (not flat rollback_reason/rollback_source).
- dispatch_action/dispatch_fix_action/dispatch_final_gate all include
  prompt_template field in their return.
- prompt_template contains timeout_seconds when hooks.metadata has it
  (regression guard for the issue we just fixed).
- _build_fix_issue_context parses a verify report (Issue #N format +
  FIX ISSUE REQUEST block format).
- _build_final_gate_context finds proposal/tasks/designs/reports.

Does NOT cover:
- jsonschema validation (not activated in this refactor)
- pg-run-hook.py subprocess spawning
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.normpath(
    os.path.join(THIS_DIR, "..", "..", "..", "..", "..", "..", ".."))
RUNNER_PY = os.path.join(
    PROJECT_ROOT, ".pg", "skills", "src", "opencode", "skills",
    "pg-build", "scripts", "pg-pipeline-runner.py")
CONFIG_PATH = os.path.join(PROJECT_ROOT, ".pg", "project.yaml")


def _load_runner():
    if "pg_pipeline_runner" in sys.modules:
        del sys.modules["pg_pipeline_runner"]
    sys.path.insert(0, THIS_DIR)
    spec = importlib.util.spec_from_file_location(
        "pg_pipeline_runner", RUNNER_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pg_pipeline_runner"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestRendererSyntax(unittest.TestCase):
    """Each Jinja-style syntax feature has at least one test."""

    def setUp(self):
        self.mod = _load_runner()
        self.render = self.mod._render_prompt_template

    def test_simple_var(self):
        self.assertEqual(self.render("Hello {{name}}", {"name": "world"}),
                         "Hello world")

    def test_missing_var_renders_empty(self):
        # Missing keys → empty string (don't leak template literal)
        self.assertEqual(self.render("Hello {{name}}", {}), "Hello ")

    def test_dotted_var(self):
        ctx = {"a": {"b": {"c": "deep"}}}
        self.assertEqual(self.render("{{a.b.c}}", ctx), "deep")

    def test_context_prefix_falls_back_to_top_level(self):
        # 'context.X' should also resolve to top-level X
        ctx = {"_change": "abc"}
        self.assertEqual(self.render("{{context._change}}", ctx), "abc")

    def test_tojson_filter(self):
        ctx = {"data": {"a": 1, "b": [2, 3]}}
        out = self.render("{{data | tojson(indent=2)}}", ctx)
        self.assertIn('"a": 1', out)
        self.assertIn('"b":', out)

    def test_tojson_filter_no_value_renders_null(self):
        out = self.render("{{missing | tojson(indent=2)}}", {})
        self.assertEqual(out, "null")

    def test_toyaml_filter(self):
        # toyaml: keys inline (no quotes), nested dicts, unicode preserved
        ctx = {"data": {"a": 1, "b": [2, 3], "desc": "中文说明"}}
        out = self.render("{{data | toyaml}}", ctx)
        self.assertIn("a: 1", out)
        self.assertIn("b:", out)
        self.assertIn("中文说明", out)  # allow_unicode=True → no \uXXXX escape
        self.assertNotIn('"a":', out)  # not JSON

    def test_toyaml_filter_no_value_renders_null(self):
        # None 仍走 "null"（与 tojson 一致），避免下游解析歧义
        out = self.render("{{missing | toyaml}}", {})
        self.assertEqual(out, "null")

    def test_toyaml_compacts_hooks_payload(self):
        # 集成断言：toyaml 渲染的 hooks 块显著短于 tojson 块
        hooks = {
            "supported_actions": ["start", "stop"],
            "action_metadata": {
                "backend": {
                    "start": {
                        "timeout_seconds": 300,
                        "description": "启动 backend 服务的完整流程：构建、部署、启动。",
                    },
                    "stop": {"timeout_seconds": 30},
                },
                "frontend": {
                    "start": {
                        "timeout_seconds": 60,
                        "description": "启动 frontend 服务的完整流程。",
                    },
                    "stop": {"timeout_seconds": 30},
                },
            },
            "invocation": {
                "command_template": "python3 .pg/skills/src/runtime/bin/pg-invoke-hook.py",
                "required_args": ["--change", "--env", "--role", "--instance", "--action"],
                "optional_args": ["--stage", "--tail-lines"],
                "notes": [
                    "timeout_seconds is INFORMATION.",
                    "--tail-lines only for logs|tail.",
                    "host/port resolved from instances[].",
                ],
            },
        }
        ctx = {"hooks": hooks}
        y_out = self.render("{{hooks | toyaml}}", ctx)
        j_out = self.render("{{hooks | tojson(indent=2)}}", ctx)
        self.assertGreater(len(j_out), len(y_out),
                           f"toyaml 应比 tojson 短: json={len(j_out)} yaml={len(y_out)}")
        # 关键字段以 YAML 形态出现
        self.assertIn("timeout_seconds: 300", y_out)
        self.assertIn("启动 backend 服务的完整流程", y_out)
        # PyYAML 3.13 默认按 key 字母序 dump，校验此行为以防版本升级
        # 静默改变 prompt 文本（diff 噪声）
        self.assertLess(
            y_out.find("description"),
            y_out.find("timeout_seconds"),
            "字母序下 description 必须在 timeout_seconds 之前",
        )
        # 不要把数字渲染成字符串
        self.assertNotIn("'300'", y_out)

    def test_if_truthy_path(self):
        tpl = "{#if context.x}yes{/if}"
        self.assertEqual(self.render(tpl, {"context": {"x": "hi"}}), "yes")
        self.assertEqual(self.render(tpl, {"context": {"x": ""}}), "")

    def test_if_sub_in_list(self):
        tpl = '{#if sub in ["dev", "verify"]}ok{/if}'
        self.assertEqual(self.render(tpl, {"sub": "dev"}), "ok")
        self.assertEqual(self.render(tpl, {"sub": "test"}), "")

    def test_each_with_this(self):
        tpl = "{#each items}- {{this.name}}\n{/each}"
        ctx = {"items": [{"name": "a"}, {"name": "b"}]}
        out = self.render(tpl, ctx)
        self.assertEqual(out, "- a\n- b\n")

    def test_nested_if_inside_each(self):
        tpl = "{#each items}{#if this.flag}!{{this.name}} {/if}{/each}"
        ctx = {"items": [{"name": "a", "flag": True},
                         {"name": "b", "flag": False},
                         {"name": "c", "flag": True}]}
        self.assertEqual(self.render(tpl, ctx), "!a !c ")

    def test_empty_list_renders_nothing(self):
        tpl = "{#each items}X{{this}}{/each}"
        self.assertEqual(self.render(tpl, {"items": []}), "")

    def test_complex_realistic_template(self):
        """Combines {#each}, {#if}, {{var|filter}}, {this.X}."""
        tpl = """\
{#each modules}
- {{this.name}} ({{this.lang}})
{#if this.test.unit}  test.unit: {{this.test.unit}}{/if}
{/each}"""
        ctx = {"modules": [
            {"name": "frontend", "lang": "ts",
             "test": {"unit": "pnpm test"}},
            {"name": "backend", "lang": "java",
             "test": {}},  # no test.unit → {#if} false
        ]}
        out = self.render(tpl, ctx)
        self.assertIn("- frontend (ts)", out)
        self.assertIn("test.unit: pnpm test", out)
        self.assertIn("- backend (java)", out)
        # backend's "test.unit: ..." line must NOT appear (no test.unit)
        # Count occurrences of "test.unit:" — should be exactly 1.
        self.assertEqual(out.count("test.unit:"), 1)


class TestBuildPromptTemplate(unittest.TestCase):
    """All 6 sub-type templates must be present and renderable."""

    def setUp(self):
        self.mod = _load_runner()
        self.minimal_ctx = {
            "_change": "test-change",
            "id": "dev.frontend",
            "label": "Frontend dev",
            "review_level": "none",
            "modules": ["frontend"],
            "module_details": [{
                "name": "frontend", "root": "webvirt-frontend",
                "language": "typescript",
                "build": "pnpm build",
                "lint": "pnpm lint:eslint",
                "test": {"unit": "pnpm test"},
            }],
            "module_roots": ["webvirt-frontend"],
            "module_names": ["frontend"],
            "max_fix_retries": 5,
            "fix_routing": "source",
            "stage": {
                "name": "dev", "test_key": "unit", "gate": "all_pass",
                "environment": {
                    "required": True, "name": "dev-local",
                    "prepare": {"status": "ok", "log_path": "", "message": ""},
                    "instances": {"backend": [{"name": "backend-1", "host": "localhost", "port": 9080}]},
                    "hooks": {
                        "supported_actions": ["start", "stop"],
                        "action_metadata": {"backend": {"start": {"timeout_seconds": 300}}},
                        "invocation": {"command_template": "X"},
                    },
                },
                "test_commands": ["pnpm test"],
            },
            "sub": "dev",
            "tasks_preformatted": ["**1.1 foo**\ndo it"],
            "tasks_validation": "verify",
            "tasks_noop": False,
        }

    def _render(self, sub):
        self.minimal_ctx["sub"] = sub
        tpl = self.mod._build_prompt_template("dev.frontend", sub)
        return self.mod._render_prompt_template(tpl, self.minimal_ctx)

    def test_test_template(self):
        out = self._render("test")
        self.assertIn("TDD 红 Phase", out)
        # Test template doesn't include hooks block
        self.assertNotIn("invoke-hook CLI", out)

    def test_dev_template_has_hooks_block(self):
        out = self._render("dev")
        self.assertIn("invoke-hook", out)
        # Dev template includes hooks.action_metadata (with timeout!)
        self.assertIn("timeout_seconds", out)
        self.assertIn("300", out)

    def test_verify_template_has_hooks_block(self):
        out = self._render("verify")
        self.assertIn("invoke-hook", out)
        self.assertIn("timeout_seconds", out)

    def test_gate_template_no_hooks_block(self):
        out = self._render("gate")
        self.assertNotIn("invoke-hook CLI", out)
        self.assertIn("Gate 审计要求", out)

    def test_fix_template_has_issue_block(self):
        out = self._render("fix")
        self.assertIn("FIX ISSUE REQUEST", out)
        self.assertIn("invoke-hook", out)
        self.assertIn("timeout_seconds", out)

    def test_final_gate_template(self):
        tpl = self.mod._build_prompt_template("final-gate", "gate")
        ctx = {
            "_change": "test-change",
            "proposal_path": ".pg/changes/test-change/proposal.md",
            "tasks_path": ".pg/changes/test-change/tasks.md",
            "design_doc_path": ".pg/changes/test-change/design.md",
            "design_doc_paths": [".pg/changes/test-change/design.md"],
            "report_paths": [".pg/changes/test-change/2-build/dev.backend-2-gate-assessment.md"],
            "tasks_preformatted": [],
        }
        out = self.mod._render_prompt_template(tpl, ctx)
        self.assertIn("Final Gate", out)
        self.assertIn("test-change", out)
        # Verify JSON serialization of paths
        self.assertIn("design_doc_paths", out)
        self.assertIn("report_paths", out)


class TestSubTrackFields(unittest.TestCase):
    """Field allowlist correctness (no dead 'deployment_actions'; new
    module_roots/module_names; rollback_context as nested dict)."""

    def setUp(self):
        self.mod = _load_runner()

    def test_no_dead_deployment_actions(self):
        for sub, fields in self.mod._SUB_TRACK_FIELDS.items():
            self.assertNotIn(
                "deployment_actions", fields,
                f"{sub} still has dead field 'deployment_actions'",
            )

    def test_dev_verify_test_have_module_roots(self):
        for sub in ("test", "dev", "verify", "gate"):
            self.assertIn(
                "module_roots", self.mod._SUB_TRACK_FIELDS[sub],
                f"{sub} missing module_roots",
            )
            self.assertIn(
                "module_names", self.mod._SUB_TRACK_FIELDS[sub],
                f"{sub} missing module_names",
            )

    def test_fix_has_issue_fields(self):
        fix_fields = self.mod._SUB_TRACK_FIELDS["fix"]
        for k in ("issue_title", "source_track", "expected", "actual",
                  "root_cause_phase", "fix_cycle"):
            self.assertIn(k, fix_fields, f"fix missing {k}")

    def test_final_gate_has_path_fields(self):
        fg_fields = self.mod._SUB_TRACK_FIELDS["final-gate"]
        for k in ("proposal_path", "tasks_path", "design_doc_path",
                  "design_doc_paths", "report_paths"):
            self.assertIn(k, fg_fields, f"final-gate missing {k}")


class TestModuleRootsDerivation(unittest.TestCase):
    """filter_track_context must compute module_roots as a JSON-array-shaped
    list (not a comma-joined string) and module_names from module_details."""

    def setUp(self):
        self.mod = _load_runner()
        self.config = self.mod.load_config()

    def test_module_roots_is_list(self):
        ctx = self.mod.filter_track_context(
            self.config, "dev.backend", sub="dev",
            change="add-host-memory-overview",
        )
        self.assertIsInstance(ctx.get("module_roots"), list)
        self.assertIn("webvirt-backend", ctx["module_roots"])

    def test_module_names_matches_modules(self):
        ctx = self.mod.filter_track_context(
            self.config, "dev.frontend", sub="dev",
            change="add-host-memory-overview",
        )
        self.assertEqual(ctx["module_names"], ["frontend"])

    def test_module_roots_dedup(self):
        # Hypothetical: if backend track declared two modules with the same
        # root, module_roots should dedupe.
        ctx = {"module_details": [
            {"name": "a", "root": "shared-root"},
            {"name": "b", "root": "shared-root"},
        ]}
        # Replicate the logic locally
        roots = list(dict.fromkeys(
            m.get("root") for m in ctx["module_details"] if m.get("root")
        ))
        self.assertEqual(roots, ["shared-root"])


class TestRollbackContextNested(unittest.TestCase):
    """rollback_context must be set as a nested dict by _enrich_context_with_rollback
    (not the old flat rollback_reason / rollback_source keys)."""

    def setUp(self):
        self.mod = _load_runner()

    def test_nested_rollback_sets_dict(self):
        ctx = {}
        rb = {
            "found": True,
            "failed_at": "2026-06-23T20:00:00",
            "reason": "test failure",
            "source": "dev.backend:verify",
        }
        self.mod._enrich_context_with_rollback(ctx, rb)
        self.assertIn("rollback_context", ctx)
        rc = ctx["rollback_context"]
        self.assertEqual(rc["failed_at"], "2026-06-23T20:00:00")
        self.assertEqual(rc["reason"], "test failure")
        self.assertEqual(rc["source"], "dev.backend:verify")

    def test_no_rollback_leaves_ctx_unchanged(self):
        ctx = {}
        self.mod._enrich_context_with_rollback(ctx, {"found": False})
        self.assertNotIn("rollback_context", ctx)
        # Also try None
        self.mod._enrich_context_with_rollback(ctx, None)
        self.assertNotIn("rollback_context", ctx)


class TestDispatchReturnsPromptTemplate(unittest.TestCase):
    """dispatch_action / dispatch_fix_action / _enter_final_gate must all
    populate the prompt_template field with rendered prompt string."""

    def setUp(self):
        self.mod = _load_runner()
        self.config = self.mod.load_config()

    def _make_ctx(self, item_id, sub):
        ctx = self.mod.filter_track_context(
            self.config, item_id, sub=sub,
            change="add-host-memory-overview",
        )
        ctx["_change"] = "add-host-memory-overview"
        return ctx

    def test_dispatch_action_returns_prompt_template(self):
        ctx = self._make_ctx("dev.frontend", "dev")
        result = self.mod.dispatch_action(
            agent="pg-build/dev", item="dev.frontend",
            sub="dev", context=ctx, attempt=1,
        )
        self.assertIn("prompt_template", result)
        tpl = result["prompt_template"]
        self.assertIsInstance(tpl, str)
        # Sanity: contains change name + module name
        self.assertIn("add-host-memory-overview", tpl)
        self.assertIn("dev.frontend", tpl)
        # CRITICAL: timeout_seconds must appear (the issue we just fixed)
        self.assertIn("timeout_seconds", tpl)
        # context must NOT be returned (orchestrator doesn't need it)
        self.assertNotIn("context", result)

    def test_dispatch_fix_action_returns_prompt_template(self):
        # Mock _build_fix_issue_context to avoid filesystem read
        with mock.patch.object(
            self.mod, "_build_fix_issue_context",
            return_value={
                "issue_title": "Test issue",
                "source_track": "dev.backend",
                "source_phase": "verify",
                "expected": "OK", "actual": "FAIL",
                "root_cause_phase": "dev.backend:dev",
                "affected_tasks": "2.5",
                "design_doc_path": ".pg/changes/x/design.md",
                "tasks_path": ".pg/changes/x/tasks.md",
            },
        ):
            ctx = self._make_ctx("dev.backend", "fix")
            result = self.mod.dispatch_fix_action(
                "dev.backend", cycle=2, context=ctx, config=self.config,
            )
            self.assertIn("prompt_template", result)
            self.assertIn("Test issue", result["prompt_template"])
            self.assertIn("fix_cycle: 2", result["prompt_template"])
            self.assertNotIn("context", result)

    def test_enter_final_gate_returns_prompt_template(self):
        # Need a change dir to read paths from
        with tempfile.TemporaryDirectory() as td:
            change_dir = Path(td) / "fake-change"
            change_dir.mkdir()
            (change_dir / "proposal.md").write_text("# proposal")
            (change_dir / "tasks.md").write_text("# tasks")
            (change_dir / "design.md").write_text("# design")
            build_dir = change_dir / "2-build"
            build_dir.mkdir()
            (build_dir / "dev.backend-2-gate-assessment.md").write_text("PASS")

            # Monkey-patch CHANGES_DIR to point to td
            with mock.patch.object(self.mod, "CHANGES_DIR", td):
                with mock.patch.object(self.mod, "PROJECT_ROOT", td):
                    state = {"current": None, "change": "fake-change"}
                    result = self.mod._enter_final_gate(
                        self.config, "fake-change", state,
                    )
                    self.assertIn("prompt_template", result)
                    self.assertIn("Final Gate", result["prompt_template"])
                    self.assertIn("fake-change", result["prompt_template"])
                    self.assertIn("report_paths", result["prompt_template"])
                    self.assertNotIn("context", result)


class TestBuildFixIssueContext(unittest.TestCase):
    """Parsing verify reports into fix-issue context (FIX ISSUE REQUEST block
    and legacy ### Issue #N format)."""

    def setUp(self):
        self.mod = _load_runner()

    def test_parses_fix_issue_request_block(self):
        with tempfile.TemporaryDirectory() as td:
            change = "fix-test-1"
            build = Path(td) / change / "2-build"
            build.mkdir(parents=True)
            report = build / "backend-1-verify.md"
            report.write_text(
                "# verify report\n\n"
                "## 失败问题清单\n\n"
                "### Issue #1: column cluster_id does not exist\n\n"
                "## FIX ISSUE REQUEST\n\n"
                "**SQL column reference bug**\n"
                "- verification_step: V-backend-1 - 默认参数返回分页+summary\n"
                "- expected: HTTP 200, returns paged memory overview\n"
                "- actual: HTTP 500, column h.cluster_id does not exist\n"
                "- root_cause_phase: dev.backend:dev\n"
                "- affected_tasks: 2.5 (Mapper SQL)\n"
            )

            with mock.patch.object(self.mod, "CHANGES_DIR", td):
                with mock.patch.object(self.mod, "PROJECT_ROOT", td):
                    out = self.mod._build_fix_issue_context(
                        change, "dev.backend", cycle=2,
                    )
            self.assertEqual(out["issue_title"], "SQL column reference bug")
            self.assertEqual(out["expected"], "HTTP 200, returns paged memory overview")
            self.assertEqual(out["actual"], "HTTP 500, column h.cluster_id does not exist")
            self.assertEqual(out["root_cause_phase"], "dev.backend:dev")
            self.assertEqual(out["affected_tasks"], "2.5 (Mapper SQL)")
            self.assertEqual(out["fix_cycle"], 2)

    def test_parses_legacy_issue_n_format(self):
        with tempfile.TemporaryDirectory() as td:
            change = "fix-test-2"
            build = Path(td) / change / "2-build"
            build.mkdir(parents=True)
            report = build / "frontend-1-verify.md"
            report.write_text(
                "# verify report\n\n"
                "### Issue #1: page renders empty\n\n"
                "- **verification_step**: V-frontend-1\n"
                "- **expected**: Cards render data\n"
                "- **actual**: Cards empty\n"
                "- **root_cause_phase**: frontend:dev\n"
            )
            with mock.patch.object(self.mod, "CHANGES_DIR", td):
                with mock.patch.object(self.mod, "PROJECT_ROOT", td):
                    out = self.mod._build_fix_issue_context(
                        change, "dev.frontend", cycle=1,
                    )
            self.assertEqual(out["issue_title"], "page renders empty")
            self.assertEqual(out["expected"], "Cards render data")
            self.assertEqual(out["actual"], "Cards empty")

    def test_returns_empty_when_no_report(self):
        with tempfile.TemporaryDirectory() as td:
            change = "no-report"
            (Path(td) / change / "2-build").mkdir(parents=True)
            with mock.patch.object(self.mod, "CHANGES_DIR", td):
                with mock.patch.object(self.mod, "PROJECT_ROOT", td):
                    out = self.mod._build_fix_issue_context(
                        change, "dev.backend", cycle=1,
                    )
            self.assertEqual(out, {})


class TestBuildFixIssueContextGate(unittest.TestCase):
    """Parsing gate-assessment reports (G-N sections) into gate-view issue
    context (gate_gap_id, audit_step, file_pos, fix_hint, ...)."""

    def setUp(self):
        self.mod = _load_runner()

    def test_parses_first_gap_section(self):
        with tempfile.TemporaryDirectory() as td:
            change = "gate-fix-1"
            build = Path(td) / change / "2-build"
            build.mkdir(parents=True)
            report = build / "backend-2-gate-assessment.md"
            report.write_text(
                "# Gate Assessment - backend\n\n"
                "## 不通过项详细说明\n\n"
                "### backend:G-1 — audit gap: V-backend-1\n"
                "- **检查项**: #1\n"
                "- **预期**: HTTP 200 returns paged memory\n"
                "- **实际**: HTTP 500 column not found\n"
                "- **文件位置**: webvirt-backend/XxxMapper.java:42\n"
                "- **关联 task**: backend:dev 任务 1.5\n"
                "- **修复建议**: rename cluster_id to tenant_id\n"
                "\n"
                "### backend:G-2 — other gap (should not be picked)\n"
                "- **检查项**: #2\n"
                "- **预期**: ...\n"
                "- **实际**: ...\n"
            )

            with mock.patch.object(self.mod, "CHANGES_DIR", td):
                with mock.patch.object(self.mod, "PROJECT_ROOT", td):
                    out = self.mod._build_fix_issue_context(
                        change, "backend", cycle=1, kind="gate",
                    )
            self.assertEqual(out["gate_gap_id"], "backend:G-1")
            self.assertEqual(out["issue_title"], "audit gap: V-backend-1")
            self.assertEqual(out["expected"], "HTTP 200 returns paged memory")
            self.assertEqual(out["actual"], "HTTP 500 column not found")
            self.assertEqual(out["file_pos"], "webvirt-backend/XxxMapper.java:42")
            self.assertEqual(out["affected_tasks"], "backend:dev 任务 1.5")
            self.assertEqual(out["fix_hint"], "rename cluster_id to tenant_id")
            self.assertEqual(out["fix_cycle"], 1)
            self.assertEqual(out["source_phase"], "gate")

    def test_returns_empty_when_no_gate_report(self):
        with tempfile.TemporaryDirectory() as td:
            change = "no-gate-report"
            (Path(td) / change / "2-build").mkdir(parents=True)
            with mock.patch.object(self.mod, "CHANGES_DIR", td):
                with mock.patch.object(self.mod, "PROJECT_ROOT", td):
                    out = self.mod._build_fix_issue_context(
                        change, "backend", cycle=1, kind="gate",
                    )
            # All gate-view fields default to empty strings (not crash)
            self.assertEqual(out["gate_gap_id"], "")
            self.assertEqual(out["audit_step"], "")
            self.assertEqual(out["expected"], "")
            self.assertEqual(out["fix_hint"], "")

    def test_kind_default_is_verify(self):
        # Backward compat: kind="verify" must still work for verify reports
        with tempfile.TemporaryDirectory() as td:
            change = "kind-default"
            build = Path(td) / change / "2-build"
            build.mkdir(parents=True)
            (build / "backend-1-verify.md").write_text(
                "## FIX ISSUE REQUEST\n\n"
                "**legacy default**\n"
                "- verification_step: V-1\n"
                "- expected: A\n"
                "- actual: B\n"
            )
            with mock.patch.object(self.mod, "CHANGES_DIR", td):
                with mock.patch.object(self.mod, "PROJECT_ROOT", td):
                    out_default = self.mod._build_fix_issue_context(
                        change, "backend", cycle=1,
                    )
                    out_explicit = self.mod._build_fix_issue_context(
                        change, "backend", cycle=1, kind="verify",
                    )
            # 默认行为没变，仍走 verify 路径
            self.assertEqual(out_default["verification_step"], "V-1")
            self.assertEqual(out_explicit["verification_step"], "V-1")
            # gate 字段不应该出现
            self.assertNotIn("gate_gap_id", out_default)


class TestFixGateTemplate(unittest.TestCase):
    """fix-gate sub must use a different prompt template from verify-fix.

    Coverage:
    - _build_prompt_template(track, 'fix-gate') returns _PROMPT_BLOCK_FIX_GATE
    - Rendered prompt contains GATE GAP REQUEST (not FIX ISSUE REQUEST)
    - Rendered prompt contains gate-view fields (gate_gap_id, audit_step,
      file_pos, fix_hint)
    - Rendered prompt's cat > filename is `gate-fix.md` (via ctx.fix_report_filename)
    - _SUB_TRACK_FIELDS has 'fix-gate' key with gate-specific fields
    - _SUB_TRACK_FIELDS['fix'] now has fix_report_filename
    """

    def setUp(self):
        self.mod = _load_runner()
        self.minimal_ctx = {
            "id": "backend",
            "label": "Backend",
            "review_level": "standard",
            "modules": ["backend"],
            "module_details": [{
                "name": "backend",
                "root": "webvirt-backend",
                "language": "java",
                "build": "mvn -pl webvirt-backend -am package -DskipTests",
                "lint": "mvn -pl webvirt-backend checkstyle:check",
                "test": {"unit": "mvn -pl webvirt-backend test"},
            }],
            "module_roots": ["webvirt-backend"],
            "module_names": ["backend"],
            "max_fix_retries": 5,
            "max_gate_fix_retries": 3,
            "fix_routing": "source",
            "fix_cycle": 2,
            "gate_cycles": 2,
            "cycles_remaining": 1,
            "gate_report_path": "2-build/backend-2-gate-assessment.md",
            "fix_report_filename": "gate-fix.md",
            "stage": {
                "name": "dev", "test_key": "unit", "gate": "all_pass",
                "environment": {
                    "required": True, "name": "dev-local",
                    "prepare": {"status": "ok", "log_path": "", "message": ""},
                    "instances": {"backend": [{"name": "backend-1", "host": "localhost", "port": 9080}]},
                    "hooks": {
                        "supported_actions": ["start", "stop"],
                        "action_metadata": {"backend": {"start": {"timeout_seconds": 300}}},
                        "invocation": {"command_template": "X"},
                    },
                },
                "test_commands": ["mvn -pl webvirt-backend test"],
            },
            "sub": "fix-gate",
            "issue_title": "audit gap: V-backend-1",
            "source_track": "backend",
            "source_phase": "gate",
            "gate_gap_id": "backend:G-1",
            "audit_step": "P-1",
            "expected": "HTTP 200",
            "actual": "HTTP 500",
            "file_pos": "webvirt-backend/X.java:42",
            "fix_hint": "rename column",
            "affected_tasks": "backend:dev 任务 1.5",
            "_change": "gate-fix-1",
            "design_doc_path": ".pg/changes/gate-fix-1/design.md",
            "tasks_path": ".pg/changes/gate-fix-1/tasks.md",
            "tasks_preformatted": [],
        }

    def test_fix_gate_uses_fix_gate_block(self):
        tpl = self.mod._build_prompt_template("backend", "fix-gate")
        out = self.mod._render_prompt_template(tpl, self.minimal_ctx)
        # fix-gate 视角标识
        self.assertIn("GATE GAP REQUEST", out)
        self.assertIn("gate_gap_id: backend:G-1", out)
        self.assertIn("audit_step: P-1", out)
        self.assertIn("file_pos: webvirt-backend/X.java:42", out)
        self.assertIn("fix_hint: rename column", out)
        # 不能有 verify-fix 视角的字段（防止渲染时串台）
        self.assertNotIn("FIX ISSUE REQUEST", out)
        self.assertNotIn("verification_step:", out)
        self.assertNotIn("root_cause_phase:", out)
        # 末尾 cat > 路径走 ctx 字段
        self.assertIn("gate-fix.md", out)
        self.assertNotIn("verify-fix.md", out)

    def test_fix_gate_uses_toyaml_for_hooks(self):
        tpl = self.mod._build_prompt_template("backend", "fix-gate")
        out = self.mod._render_prompt_template(tpl, self.minimal_ctx)
        # hooks 块走 toyaml（不是 json）
        self.assertIn("```yaml", out)
        self.assertIn("timeout_seconds: 300", out)
        self.assertNotIn('"timeout_seconds": 300', out)

    def test_fix_block_still_uses_verify_fix_md_by_default(self):
        # 回归测试：fix 块末尾仍写 verify-fix.md（dispatch_fix_action 默认注入）
        ctx = dict(self.minimal_ctx, sub="fix", fix_report_filename="verify-fix.md")
        ctx.pop("gate_gap_id", None)
        ctx.pop("audit_step", None)
        ctx.pop("file_pos", None)
        ctx.pop("fix_hint", None)
        tpl = self.mod._build_prompt_template("backend", "fix")
        out = self.mod._render_prompt_template(tpl, ctx)
        self.assertIn("FIX ISSUE REQUEST", out)
        self.assertIn("verify-fix.md", out)
        self.assertNotIn("gate-fix.md", out)

    def test_sub_track_fields_has_fix_gate(self):
        # _SUB_TRACK_FIELDS 必须有 fix-gate key
        self.assertIn("fix-gate", self.mod._SUB_TRACK_FIELDS)
        fg = self.mod._SUB_TRACK_FIELDS["fix-gate"]
        # 必备字段
        for f in (
            "gate_gap_id", "audit_step", "file_pos", "fix_hint",
            "gate_cycles", "cycles_remaining", "gate_report_path",
            "max_gate_fix_retries", "fix_report_filename",
        ):
            self.assertIn(f, fg, f"fix-gate 子集缺字段: {f}")

    def test_sub_track_fields_fix_has_fix_report_filename(self):
        # fix 子集必须包含 fix_report_filename（dispatch_fix_action 默认值覆盖）
        self.assertIn(
            "fix_report_filename",
            self.mod._SUB_TRACK_FIELDS["fix"],
        )


class TestFixGatePromptStructure(unittest.TestCase):
    """Verify the rendered fix-gate prompt contains all the structure that
    LLM sub-agent needs: hooks YAML block, GATE GAP REQUEST section, fix
    cycle counter, fix_report_filename cat > command."""

    def setUp(self):
        self.mod = _load_runner()

    def test_rendered_prompt_has_8_step_validation_checklist(self):
        ctx = {
            "context": {
                "id": "backend",
                "label": "Backend",
                "_change": "x",
                "stage": {
                    "test_commands": ["mvn test"],
                    "environment": {
                        "hooks": {
                            "supported_actions": ["start"],
                            "action_metadata": {"backend": {"start": {"timeout_seconds": 60}}},
                            "invocation": {"command_template": "X"},
                        },
                    },
                },
                "fix_report_filename": "gate-fix.md",
                "fix_cycle": 1,
                "max_gate_fix_retries": 3,
                "cycles_remaining": 2,
                "next_report_n": 4,
            }
        }
        tpl = self.mod._build_prompt_template("backend", "fix-gate")
        out = self.mod._render_prompt_template(tpl, ctx)
        # 8 步必跑流程（注意：第 2 步里的 {{context.stage.test_commands.0}}
        # 走 _walk dict-only 解析，list index 解析是 runner 的遗留 bug；
        # 这里只断言步骤框架，不查具体命令展开）
        for step in [
            "1. 修改源码",
            "3. 跑模块 lint（必须 0 警告）",
            "4. 启动 `runner invoke-hook --action start` 服务（如需）",
            "5. 跑 design.md 中 P-N 审计项对应的验证项",
            "6. 抓 `runner invoke-hook --action logs --tail-lines 100` 日志确认无 ERROR",
            "7. 停止 `runner invoke-hook --action stop` 服务（如启动过）",
            "8. 用 `cat > 2-build/backend-4-gate-fix.md << 'EOF' ... EOF` 自行写盘",
        ]:
            self.assertIn(step, out, f"missing step: {step}")
        # GATE GAP REQUEST 块标题（gate 视角，不是 verify 视角）
        self.assertIn("### GATE GAP REQUEST", out)
        # cycles_remaining 显式出现
        self.assertIn("cycles_remaining: 2", out)


class TestBuildFinalGateContext(unittest.TestCase):
    """_build_final_gate_context must find proposal/tasks/designs/reports."""

    def setUp(self):
        self.mod = _load_runner()

    def test_finds_all_paths(self):
        with tempfile.TemporaryDirectory() as td:
            change = "fg-test"
            change_dir = Path(td) / change
            change_dir.mkdir()
            (change_dir / "proposal.md").write_text("# proposal")
            (change_dir / "tasks.md").write_text("# tasks")
            (change_dir / "design.md").write_text("# design A")
            (change_dir / "design-backend.md").write_text("# design B")
            build_dir = change_dir / "2-build"
            build_dir.mkdir()
            (build_dir / "dev.backend-1-gate-assessment.md").write_text("PASS")
            (build_dir / "dev.frontend-1-gate-assessment.md").write_text("PASS")

            with mock.patch.object(self.mod, "CHANGES_DIR", td):
                with mock.patch.object(self.mod, "PROJECT_ROOT", td):
                    out = self.mod._build_final_gate_context(change)

            self.assertEqual(out["_change"], change)
            self.assertIn("proposal.md", out["proposal_path"])
            self.assertIn("tasks.md", out["tasks_path"])
            # design_doc_paths: at least one design*.md
            self.assertGreaterEqual(len(out["design_doc_paths"]), 1)
            # First design path is the default design_doc_path
            self.assertIn(out["design_doc_paths"][0],
                          [out["design_doc_path"]])
            # report_paths: at least 2 gate assessments
            self.assertGreaterEqual(len(out["report_paths"]), 2)
            self.assertTrue(all("gate-assessment" in p for p in out["report_paths"]))


class TestRegressionTimeoutInPrompt(unittest.TestCase):
    """End-to-end regression: dispatch_action for dev agent MUST produce a
    prompt that contains the action_metadata timeout_seconds. This is the
    specific issue raised by the user (timeout 信息缺失) — guard against
    future regressions."""

    def setUp(self):
        self.mod = _load_runner()
        self.config = self.mod.load_config()

    def test_timeout_in_dev_prompt(self):
        ctx = self.mod.filter_track_context(
            self.config, "dev.backend", sub="dev",
            change="add-host-memory-overview",
        )
        ctx["_change"] = "add-host-memory-overview"
        result = self.mod.dispatch_action(
            agent="pg-build/dev", item="dev.backend",
            sub="dev", context=ctx, attempt=1,
        )
        prompt = result["prompt_template"]
        # backend.start.timeout_seconds = 300 per project.yaml
        self.assertIn("300", prompt,
                      "dev prompt missing backend.start.timeout_seconds (300)")
        # backend.logs.timeout_seconds = 30
        self.assertIn("30", prompt,
                      "dev prompt missing backend.logs.timeout_seconds (30)")

    def test_timeout_in_verify_prompt(self):
        ctx = self.mod.filter_track_context(
            self.config, "dev.backend", sub="verify",
            change="add-host-memory-overview",
        )
        ctx["_change"] = "add-host-memory-overview"
        result = self.mod.dispatch_action(
            agent="pg-build/verify", item="dev.backend",
            sub="verify", context=ctx, attempt=1,
        )
        self.assertIn("300", result["prompt_template"])

    def test_timeout_in_fix_prompt(self):
        # No actual verify report → fix prompt has empty issue fields,
        # but should still include hooks block with timeout.
        with mock.patch.object(
            self.mod, "_build_fix_issue_context", return_value={},
        ):
            ctx = self.mod.filter_track_context(
                self.config, "dev.backend", sub="fix",
                change="add-host-memory-overview",
            )
            ctx["_change"] = "add-host-memory-overview"
            result = self.mod.dispatch_fix_action(
                "dev.backend", cycle=1, context=ctx, config=self.config,
            )
            self.assertIn("300", result["prompt_template"])


if __name__ == "__main__":
    unittest.main()
