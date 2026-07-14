"""Unit tests for v3.x dispatcher rule_docs injection.

修复 review agent 收不到 .pg/code-review/<profile>/*.md 的死代码问题。

覆盖：
- dispatch.build_ctx(phase='review') 把 markdown 规则注入 ctx
- 非 review phase 不注入 code_review_rule_docs（避免误污染）
- p0_check_names 出现在 ctx.code_review_p0_checks
- rule_docs_yaml 块正确格式化（按 check name 排序）
- 实现完整性 P0 FAIL 强制 escalate（reducer 单元测试）
- parse_p0_failures 从 summary 解析
"""

import os
import sys
import tempfile
import textwrap
import unittest


sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "scripts",
    ),
)


class TestParseP0Failures(unittest.TestCase):
    """sub_agent_contract.parse_p0_failures 解析."""

    def test_empty(self):
        from pipeline.sub_agent_contract import parse_p0_failures
        self.assertEqual(parse_p0_failures(""), ())
        self.assertEqual(parse_p0_failures("review_score: 90, p0_failures: []"), ())

    def test_single(self):
        from pipeline.sub_agent_contract import parse_p0_failures
        self.assertEqual(
            parse_p0_failures("review_score: 90, p0_failures: [R-1]"),
            ("R-1",),
        )

    def test_multiple(self):
        from pipeline.sub_agent_contract import parse_p0_failures
        self.assertEqual(
            parse_p0_failures("review_score: 60, p0_failures: [R-2, R-4]"),
            ("R-2", "R-4"),
        )

    def test_with_quotes(self):
        from pipeline.sub_agent_contract import parse_p0_failures
        self.assertEqual(
            parse_p0_failures("gate_score: 90, p0_failures: ['G-1', \"G-2\"]"),
            ("G-1", "G-2"),
        )

    def test_missing(self):
        from pipeline.sub_agent_contract import parse_p0_failures
        self.assertEqual(parse_p0_failures("no p0_failures here"), ())


class TestProfileLoaderP0(unittest.TestCase):
    """profile_loader 加载 p0 字段."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # profile_loader 期望 .pg/code-review/code-review.yaml 在 project_root 下
        cr_dir = os.path.join(self.tmp, ".pg", "code-review")
        os.makedirs(cr_dir, exist_ok=True)
        # 写最小 yaml profile
        with open(os.path.join(cr_dir, "code-review.yaml"), "w") as f:
            f.write(textwrap.dedent("""\
                profiles:
                  default:
                    checks:
                      design_alignment:
                        enabled: true
                        weight: 30
                      implementation_completeness:
                        enabled: true
                        weight: 25
                        p0: true
                """))

    def test_p0_loaded(self):
        from pipeline.profile_loader import load_effective_profile
        p = load_effective_profile(self.tmp, ["default"])
        ic = p.get_check("implementation_completeness")
        self.assertIsNotNone(ic)
        self.assertTrue(ic.p0)
        da = p.get_check("design_alignment")
        self.assertIsNotNone(da)
        self.assertFalse(da.p0)

    def test_p0_check_names(self):
        from pipeline.profile_loader import load_effective_profile
        p = load_effective_profile(self.tmp, ["default"])
        self.assertEqual(p.p0_check_names(), ("implementation_completeness",))

    def test_p0_or_merge_across_profiles(self):
        """子 profile 设 p0=true，merge 后必须保留 p0。"""
        # 新增子 profile extend default
        cr_dir = os.path.join(self.tmp, ".pg", "code-review")
        with open(os.path.join(cr_dir, "code-review.yaml"), "w") as f:
            f.write(textwrap.dedent("""\
                profiles:
                  default:
                    checks:
                      design_alignment:
                        enabled: true
                        weight: 30
                      implementation_completeness:
                        enabled: true
                        weight: 25
                        p0: true
                  java-spring:
                    inherit: default
                    checks:
                      implementation_completeness:
                        enabled: true
                        weight: 25
                        p0: false
                """))
        from pipeline.profile_loader import load_effective_profile
        p = load_effective_profile(self.tmp, ["java-spring"])
        ic = p.get_check("implementation_completeness")
        self.assertIsNotNone(ic, "p0 check 必须存在")
        self.assertTrue(ic.p0, "OR merge: p0=true 应保留")


class TestDispatcherRuleDocsInjection(unittest.TestCase):
    """dispatch.build_ctx 在 review phase 注入 rule_docs."""

    def setUp(self):
        self.proj_root = tempfile.mkdtemp()
        # 准备 code-review profile + rule docs
        cr_dir = os.path.join(self.proj_root, ".pg", "code-review")
        os.makedirs(os.path.join(cr_dir, "default"), exist_ok=True)
        with open(os.path.join(cr_dir, "code-review.yaml"), "w") as f:
            f.write(textwrap.dedent("""\
                profiles:
                  default:
                    checks:
                      design_alignment:
                        enabled: true
                        weight: 30
                        doc: design_alignment
                      implementation_completeness:
                        enabled: true
                        weight: 25
                        doc: implementation_completeness
                        p0: true
                """))
        # rule docs
        with open(os.path.join(cr_dir, "default", "design_alignment.md"), "w") as f:
            f.write("# design_alignment rule\n\nFAIL 判定...")
        with open(os.path.join(cr_dir, "default", "implementation_completeness.md"), "w") as f:
            f.write("# implementation_completeness rule\n\nP0 硬约束...")

    def test_review_phase_injects_rule_docs(self):
        from pipeline.state import (
            PipelineState, TrackState, PhaseState, SUB_PHASES,
        )
        from pipeline.dispatch import build_ctx

        # 用 default profile 直接（避免 java-spring 的 inherit 链）
        t = TrackState.create(
            track_id="dev.backend",
            code_review_enabled=True,
            code_review_profiles=("default",),
        )
        state = PipelineState(
            change="test-change",
            tracks={"dev.backend": t},
            current_track="dev.backend",
            current_phase="review",
        )
        ctx = build_ctx(
            state, "dev.backend", "review",
            change_root=self.proj_root, project_root=self.proj_root,
        )
        # 验证注入
        self.assertIn("code_review_rule_docs", ctx)
        rule_docs = ctx["code_review_rule_docs"]
        self.assertIn("design_alignment", rule_docs)
        self.assertIn("implementation_completeness", rule_docs)
        self.assertIn("P0 硬约束", rule_docs["implementation_completeness"])

        # 验证 p0_checks
        self.assertIn("code_review_p0_checks", ctx)
        self.assertEqual(
            ctx["code_review_p0_checks"],
            ["implementation_completeness"],
        )

        # 验证 yaml block 已格式化
        self.assertIn("code_review_rule_docs_yaml", ctx)
        self.assertIn("#### design_alignment", ctx["code_review_rule_docs_yaml"])
        self.assertIn("#### implementation_completeness", ctx["code_review_rule_docs_yaml"])

    def test_non_review_phase_no_injection(self):
        from pipeline.state import PipelineState, TrackState
        from pipeline.dispatch import build_ctx

        t = TrackState.create(
            track_id="dev.backend",
            code_review_profiles=("default",),
        )
        state = PipelineState(
            change="test-change",
            tracks={"dev.backend": t},
        )
        ctx = build_ctx(
            state, "dev.backend", "verify",
            change_root=self.proj_root, project_root=self.proj_root,
        )
        self.assertNotIn("code_review_rule_docs", ctx)
        self.assertNotIn("code_review_p0_checks", ctx)


class TestRendererPlaceholders(unittest.TestCase):
    """renderer.render_dispatch 替换 __RULE_DOCS_PLACEHOLDER__ 与 __P0_CHECKS_PLACEHOLDER__."""

    def test_placeholders_substituted(self):
        from template_engine.renderer import render_dispatch
        ctx = {
            "id": "dev.backend:review",
            "_change": "test-change",
            "label": "dev.backend review",
            "modules": ["backend"],
            "module_roots": "['webvirt-backend']",
            "module_details": "x",
            "max_fix_retries": 5,
            "stage_name": "dev",
            "test_key": "unit",
            "gate": "all_pass",
            "env_required": True,
            "env_name": "dev-local",
            "prepare_status": "ok",
            "prepare_log_path": "",
            "test_commands": "x",
            "env_instances_block": "x",
            "hooks_block": "x",
            "env_instances": "x",
            "hooks_yaml": "x",
            "phase": "review",
            "cycle": 1,
            "attempt": 1,
            "report_filename": "x",
            "report_seq": "001",
            "tasks_preformatted": "x",
            "tasks_validation": "x",
            "code_review_rule_docs_yaml": "## fake rule docs content",
            "code_review_p0_checks": ["implementation_completeness", "error_silence"],
            "build_rules_prepend": "",
            "build_rules_append": "",
        }
        out = render_dispatch("review", ctx)
        self.assertIn("## fake rule docs content", out)
        self.assertIn("implementation_completeness, error_silence", out)
        # 占位符不应残留
        self.assertNotIn("__RULE_DOCS_PLACEHOLDER__", out)
        self.assertNotIn("__P0_CHECKS_PLACEHOLDER__", out)


class TestReducerImplementationCompletenessEscalate(unittest.TestCase):
    """reducer._handle_review: implementation_completeness P0 → force escalate."""

    def _make_state_and_record(self, summary: str):
        from pipeline.state import PipelineState, TrackState, PhaseState
        from pipeline.events import PipelineRecord, STATUS_COMPLETED

        t = TrackState.create(
            track_id="dev.backend",
            code_review_languages=("java",),
            max_review_fix_retries=3,
        )
        # 把 review phase 标为 running
        t = t.replace(phases={
            "test": PhaseState(status="completed"),
            "dev": PhaseState(status="completed"),
            "review": PhaseState(status="pending", attempt=1),
        })
        state = PipelineState(
            change="test-change",
            tracks={"dev.backend": t},
            current_track="dev.backend",
            current_phase="review",
        )
        record = PipelineRecord(
            track="dev.backend",
            phase="review",
            status=STATUS_COMPLETED,
            summary=summary,
            report_path="/tmp/review.md",
            tasks_updated=("R-6",),  # R-6 = implementation_completeness
        )
        return state, record

    def test_completed_with_p0_ic_triggers_escalate(self):
        from pipeline.reducer import _handle_review
        state, record = self._make_state_and_record(
            "review_score: 100, p0_failures: [implementation_completeness]"
        )
        new_state, action = _handle_review(state, record)
        # action 应该是 dispatch fix-review（不是 dispatch verify）
        self.assertIn(action.kind, ("dispatch", "advance"))
        # 验证新 phase 是 fix-review（不是 verify）
        if action.kind == "dispatch":
            self.assertEqual(action.phase, "fix-review")
        else:
            # advance 路径应继续走 escalate 分支，下游推进到 fix-review
            self.assertIn(new_state.current_phase, ("fix-review",))

    def test_completed_without_p0_passes_through(self):
        from pipeline.reducer import _handle_review
        state, record = self._make_state_and_record(
            "review_score: 100, p0_failures: []"
        )
        new_state, action = _handle_review(state, record)
        # 应推进到 verify
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, "verify")


if __name__ == "__main__":
    unittest.main()
