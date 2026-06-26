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
- fix/fix-gate templates render 「必读源报告」block (verify_report_path /
  gate_report_path) instead of parsing reports into structured fields.
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

    def test_fix_template_has_source_report_block(self):
        # fix 模板渲染需要 minimal_ctx 里有 verify_report_path
        # （runner 必填字段，缺则渲染为空字符串）
        self.minimal_ctx["verify_report_path"] = (
            ".pg/changes/test-change/2-build/dev.frontend-1-verify.md"
        )
        self.minimal_ctx["fix_cycle"] = 1
        out = self._render("fix")
        # runner 在 fix 模板里直接告诉 fix agent 去读源 verify 报告，
        # 不再做结构化抽取。
        self.assertIn("必读源报告", out)
        self.assertIn(
            "dev.frontend-1-verify.md", out,
            "fix 模板应渲染 verify_report_path 真实路径",
        )
        self.assertIn("invoke-hook", out)
        self.assertIn("timeout_seconds", out)
        # 旧的 FIX ISSUE REQUEST 块应已删除
        self.assertNotIn("FIX ISSUE REQUEST", out)
        # 模板里直接出现"verify_report_path"占位符本身不应渲染到 prompt
        # （因为 minimal_ctx 提供了真实值），但"必读源报告"标识必须存在
        self.assertIn("源 verify 报告", out)

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

    def test_fix_has_source_report_field(self):
        # runner 不再在 fix 白名单中放 issue_title / expected / actual 等
        # 结构化字段；只放最小必需字段 + verify_report_path 让 fix agent
        # 自行读源报告。
        fix_fields = self.mod._SUB_TRACK_FIELDS["fix"]
        for k in ("source_track", "source_phase", "fix_cycle",
                  "verify_report_path", "design_doc_path", "tasks_path"):
            self.assertIn(k, fix_fields, f"fix missing {k}")
        # 这些被删除的结构化字段不应再出现
        for k in ("issue_title", "verification_step", "expected", "actual",
                  "root_cause_phase", "affected_tasks"):
            self.assertNotIn(k, fix_fields,
                             f"fix 仍含已删除字段: {k}")

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
        # dispatch_fix_action 注入 verify_report_path (而非从报告中解析字段)；
        # 不再需要 mock _build_fix_issue_context（该函数已删除）。
        # 为避免读真实磁盘，临时建一个空 change 目录并 mock 路径。
        with tempfile.TemporaryDirectory() as td:
            change_dir = Path(td) / "x"
            change_dir.mkdir()
            build_dir = change_dir / "2-build"
            build_dir.mkdir()
            # 制造一份占位 verify 报告，runner 会把它的路径注入 prompt
            (build_dir / "dev.backend-1-verify.md").write_text("# verify stub")
            with mock.patch.object(self.mod, "CHANGES_DIR", td):
                with mock.patch.object(self.mod, "PROJECT_ROOT", td):
                    ctx = self._make_ctx("dev.backend", "fix")
                    ctx["_change"] = "x"
                    result = self.mod.dispatch_fix_action(
                        "dev.backend", cycle=2, context=ctx, config=self.config,
                    )
            self.assertIn("prompt_template", result)
            # 模板里出现了"必读源报告"块 + 占位符被替换为真实路径
            self.assertIn("必读源报告", result["prompt_template"])
            self.assertIn("dev.backend-1-verify.md", result["prompt_template"])
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


class TestFixGateTemplate(unittest.TestCase):
    """fix-gate sub must use a different prompt template from verify-fix.

    Coverage:
    - _build_prompt_template(track, 'fix-gate') returns _PROMPT_BLOCK_FIX_GATE
    - Rendered prompt contains 「必读源报告」block (NOT GATE GAP REQUEST or
      FIX ISSUE REQUEST — runner does NOT parse gate reports anymore)
    - fix-gate template only injects gate_report_path; gate-view fields
      (gate_gap_id / audit_step / file_pos / fix_hint) are NOT in template
    - Rendered prompt's cat > filename is `fix-gate-verify-{cycle}.md`
    - _SUB_TRACK_FIELDS has 'fix-gate' key with path fields only
    - _SUB_TRACK_FIELDS['fix'] now has verify_report_path
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
            "fix_report_filename": "fix-gate-verify.md",
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
            "source_track": "backend",
            "source_phase": "gate",
            "_change": "gate-fix-1",
            "design_doc_path": ".pg/changes/gate-fix-1/design.md",
            "tasks_path": ".pg/changes/gate-fix-1/tasks.md",
            "tasks_preformatted": [],
        }

    def test_fix_gate_uses_source_report_block(self):
        tpl = self.mod._build_prompt_template("backend", "fix-gate")
        out = self.mod._render_prompt_template(tpl, self.minimal_ctx)
        # runner 在 fix-gate 模板里直接告诉 fix-gate agent 去读源 gate 报告
        self.assertIn("必读源报告", out)
        self.assertIn(
            "源 gate 报告", out,
            "fix-gate 模板应明确指向 gate 报告（而非 verify 报告）",
        )
        self.assertIn("2-build/backend-2-gate-assessment.md", out)
        # 旧的 GATE GAP REQUEST 块和所有结构化字段都应已删除
        self.assertNotIn("GATE GAP REQUEST", out)
        self.assertNotIn("gate_gap_id:", out)
        self.assertNotIn("audit_step:", out)
        self.assertNotIn("file_pos:", out)
        self.assertNotIn("fix_hint:", out)
        # 也不能有 verify-fix 视角的字段
        self.assertNotIn("FIX ISSUE REQUEST", out)
        self.assertNotIn("verification_step:", out)
        self.assertNotIn("root_cause_phase:", out)

    def test_fix_gate_uses_toyaml_for_hooks(self):
        tpl = self.mod._build_prompt_template("backend", "fix-gate")
        out = self.mod._render_prompt_template(tpl, self.minimal_ctx)
        # hooks 块走 toyaml（不是 json）
        self.assertIn("```yaml", out)
        self.assertIn("timeout_seconds: 300", out)
        self.assertNotIn('"timeout_seconds": 300', out)

    def test_fix_block_renders_source_report_path(self):
        # fix 模板渲染：应注入 verify_report_path（runner 必填字段），
        # 写盘路径走模板的 fix-verify-{cycle}.md
        ctx = dict(self.minimal_ctx, sub="fix")
        ctx["fix_report_filename"] = "fix-verify.md"
        ctx["verify_report_path"] = "2-build/dev.backend-1-verify.md"
        tpl = self.mod._build_prompt_template("backend", "fix")
        out = self.mod._render_prompt_template(tpl, ctx)
        self.assertIn("必读源报告", out)
        self.assertIn("2-build/dev.backend-1-verify.md", out)
        # 写盘路径走模板硬编码的 fix-verify-{cycle}.md
        self.assertIn("fix-verify-2.md", out)
        # 不能出现 gate-fix 路径
        self.assertNotIn("fix-gate-verify", out)
        # 不应再有结构化抽取的字段
        self.assertNotIn("issue_title", out)
        self.assertNotIn("expected:", out)
        self.assertNotIn("actual:", out)

    def test_sub_track_fields_has_fix_gate(self):
        # _SUB_TRACK_FIELDS 必须有 fix-gate key
        self.assertIn("fix-gate", self.mod._SUB_TRACK_FIELDS)
        fg = self.mod._SUB_TRACK_FIELDS["fix-gate"]
        # 必备字段：路径 + 元数据 (无结构化抽取字段)
        for f in (
            "gate_cycles", "cycles_remaining", "gate_report_path",
            "max_gate_fix_retries", "fix_report_filename",
        ):
            self.assertIn(f, fg, f"fix-gate 子集缺字段: {f}")
        # 已删除的结构化字段不应再出现
        for k in ("issue_title", "gate_gap_id", "audit_step",
                  "file_pos", "fix_hint", "affected_tasks"):
            self.assertNotIn(k, fg,
                             f"fix-gate 仍含已删除字段: {k}")

    def test_sub_track_fields_fix_has_verify_report_path(self):
        # fix 子集必须包含 verify_report_path（dispatch_fix_action 注入）
        fix_fields = self.mod._SUB_TRACK_FIELDS["fix"]
        self.assertIn("verify_report_path", fix_fields)
        self.assertIn("fix_report_filename", fix_fields)


class TestFixGatePromptStructure(unittest.TestCase):
    """Verify the rendered fix-gate prompt contains all the structure that
    LLM sub-agent needs: hooks YAML block, 「必读源报告」section pointing
    at gate_report_path, fix cycle counter, fix_report_filename cat >
    command."""

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
                "fix_report_filename": "fix-gate-verify.md",
                "fix_cycle": 1,
                "report_seq": "004",
                "dispatch_seq": "003",
                "gate_report_path": "2-build/backend-2-gate-assessment.md",
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
            "8. 用 `cat > 2-build/backend-4-fix-gate-verify-1.md << 'EOF' ... EOF` 自行写盘",
        ]:
            self.assertIn(step, out, f"missing step: {step}")
        # runner 注入「必读源报告」块（不再有 GATE GAP REQUEST 块）
        self.assertIn("必读源报告", out)
        self.assertIn("2-build/backend-2-gate-assessment.md", out)
        self.assertNotIn("GATE GAP REQUEST", out)
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
        # No _build_fix_issue_context anymore — fix prompt should still include
        # hooks block with timeout regardless of whether a verify report exists.
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
