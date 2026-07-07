"""Unit tests for renderer.py — v2.2 dispatch 提示词结构优化。

覆盖：
- PHASES_WITH_ENV (test/dev/verify/fix/fix-gate) 注入 env.hooks + 运行时环境操作指令
- PHASES_WITHOUT_ENV (gate/simple/final-gate) 不注入这些块
- 标题简化：## 任务：{id} 不再带 - {label}
- 末尾无旧"返回格式"段（由 sub_agent_contract.yaml 块取代）
"""

import os
import sys
import unittest


sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "scripts",
    ),
)

from template_engine.renderer import (
    render_dispatch,
    PHASES_WITH_ENV,
    PHASES_WITHOUT_ENV,
)


def _ctx(phase: str = "test") -> dict:
    """构造最小可用 ctx 用于 render_dispatch。"""
    return {
        "id": "dev.backend:test",
        "_change": "test-change",
        "label": "测试阶段（仅用于 title 检测不被注入）",
        "modules": ["backend"],
        "module_details": "- module: backend\n  - root: webvirt-backend\n  - language: java",
        "module_roots": "['webvirt-backend']",
        "review_level": "standard",
        "max_fix_retries": 5,
        "stage_name": "dev",
        "test_key": "unit",
        "gate": "all_pass",
        "env_required": True,
        "env_name": "dev-local",
        "prepare_status": "ok",
        "prepare_log_path": "",
        "test_commands": "cd webvirt-backend && mvn test",
        "env_instances_block": (
            "- stage.environment.instances:\n```yaml\nbackend:\n- host: localhost\n  name: backend-1\n```\n"
        ),
        "hooks_block": (
            "- stage.environment.hooks:\n```yaml\nbackend:\n  start:\n    script: .pg/hooks/role-backend-start.sh\n```\n"
        ),
        "env_instances": (
            "backend:\n- host: localhost\n  name: backend-1\n"
        ),
        "hooks_yaml": (
            "backend:\n  start:\n    script: .pg/hooks/role-backend-start.sh\n"
        ),
        "phase": phase,
        "cycle": 1,
        "attempt": 1,
        "report_filename": "001-dev.backend-test.md",
        "report_seq": "001",
        "tasks_preformatted": "## 1. dev.backend:test\n- [ ] 1.1 test",
        "tasks_validation": "| V-1 | verify | method | result |",
    }


class TestPhaseEnvClassification(unittest.TestCase):
    """PHASES_WITH_ENV / PHASES_WITHOUT_ENV 分类正确性。

    v2.2 调整：test phase 不再属于 WITH_ENV（test 阶段只需要写测试代码 + 跑 mvn test，
    不需要手动起停服务或查日志——服务由编排器 hook 管理）。
    """

    def test_with_env_phases_contains_expected(self):
        self.assertEqual(
            PHASES_WITH_ENV,
            frozenset({"dev", "verify", "fix", "fix-gate"}),
        )

    def test_without_env_phases_contains_expected(self):
        self.assertEqual(
            PHASES_WITHOUT_ENV,
            frozenset({"test", "gate", "simple", "final-gate"}),
        )

    def test_phases_disjoint(self):
        self.assertEqual(
            PHASES_WITH_ENV & PHASES_WITHOUT_ENV,
            frozenset(),
        )

    def test_all_known_phases_classified(self):
        """所有已知 phase 必须属于其中一个集合（确保调度全覆盖）。"""
        all_phases = {"test", "dev", "verify", "gate", "fix", "fix-gate", "simple", "final-gate"}
        self.assertEqual(
            PHASES_WITH_ENV | PHASES_WITHOUT_ENV,
            frozenset(all_phases),
        )


class TestEnvBlockInjection(unittest.TestCase):
    """env.hooks + 运行时环境操作指令 按 phase 注入。"""

    def test_verify_injects_env_hooks(self):
        content = render_dispatch("verify", _ctx("verify"))
        # env.hooks 块
        self.assertIn("stage.environment.hooks", content)
        self.assertIn(".pg/hooks/role-backend-start.sh", content)
        # env.instances 块
        self.assertIn("stage.environment.instances", content)
        # 运行时环境操作指令（v2.2 新标题）
        self.assertIn("运行时环境操作指令", content)

    def test_test_skips_env_hooks(self):
        """v2.2: test 阶段不需要手动起停服务，跳过 env 配置注入。"""
        content = render_dispatch("test", _ctx("test"))
        self.assertNotIn("stage.environment.hooks", content)
        self.assertNotIn("stage.environment.instances", content)
        self.assertNotIn("运行时环境操作指令", content)

    def test_dev_injects_env_hooks(self):
        content = render_dispatch("dev", _ctx("dev"))
        self.assertIn("stage.environment.hooks", content)
        self.assertIn("运行时环境操作指令", content)

    def test_fix_injects_env_hooks(self):
        content = render_dispatch("fix", _ctx("fix"))
        self.assertIn("stage.environment.hooks", content)
        self.assertIn("运行时环境操作指令", content)

    def test_fix_gate_injects_env_hooks(self):
        content = render_dispatch("fix-gate", _ctx("fix-gate"))
        self.assertIn("stage.environment.hooks", content)
        self.assertIn("运行时环境操作指令", content)

    def test_gate_skips_env_hooks(self):
        content = render_dispatch("gate", _ctx("gate"))
        # env.hooks / env.instances / 运行时环境操作指令 都不应出现
        self.assertNotIn("stage.environment.hooks", content)
        self.assertNotIn("stage.environment.instances", content)
        self.assertNotIn("运行时环境操作指令", content)

    def test_simple_skips_env_hooks(self):
        content = render_dispatch("simple", _ctx("simple"))
        self.assertNotIn("stage.environment.hooks", content)
        self.assertNotIn("stage.environment.instances", content)
        self.assertNotIn("运行时环境操作指令", content)

    def test_final_gate_skips_env_hooks(self):
        content = render_dispatch("final-gate", _ctx("final-gate"))
        self.assertNotIn("stage.environment.hooks", content)
        self.assertNotIn("stage.environment.instances", content)
        self.assertNotIn("运行时环境操作指令", content)


class TestEnvBlockPosition(unittest.TestCase):
    """v2.2: 运行时环境操作指令紧跟 Stage 配置段（在 phase template 之前）。"""

    def test_env_block_immediately_after_stage_config(self):
        content = render_dispatch("verify", _ctx("verify"))
        # 位置约束: Stage 配置段 必须在 运行时环境操作指令 之前
        stage_pos = content.find("### Stage 配置")
        env_op_pos = content.find("### 运行时环境操作指令")
        self.assertGreater(stage_pos, 0, "应含 ### Stage 配置 段")
        self.assertGreater(env_op_pos, 0, "应含 ### 运行时环境操作指令 段")
        self.assertGreater(env_op_pos, stage_pos,
                           "运行时环境操作指令应紧跟 Stage 配置之后")

    def test_env_block_after_phase_template(self):
        """运行时环境操作指令必须在 phase template 之前（与 base header 一起）。"""
        content = render_dispatch("dev", _ctx("dev"))
        env_op_pos = content.find("### 运行时环境操作指令")
        phase_marker_pos = content.find("### 实现要求")  # dev.yaml marker
        # 运行时操作指令应在 phase template 之前（即在 base header_env 段）
        self.assertGreater(env_op_pos, 0)
        # phase template 应在 运行时操作指令 之后
        if phase_marker_pos > 0:
            self.assertGreater(phase_marker_pos, env_op_pos)


class TestRoleInstanceDocumentation(unittest.TestCase):
    """v2.2: ROLE/INSTANCE 字段来源解释必须出现在 env block 中。"""

    def test_role_source_explained(self):
        content = render_dispatch("verify", _ctx("verify"))
        self.assertIn("ROLE", content)
        self.assertIn("INSTANCE", content)
        # 应解释 ROLE 从 hooks 顶层 key 选取
        self.assertIn("stage.environment.hooks", content)
        # 应解释 INSTANCE 从 instances.<role> 的 name 字段选取
        self.assertIn("name", content)

    def test_example_invocation_present(self):
        """header_env 应提供具体调用示例。"""
        content = render_dispatch("verify", _ctx("verify"))
        # 应含至少一个 --action start 例子
        self.assertIn("--action start", content)


class TestTitleSimplification(unittest.TestCase):
    """标题 ## 任务：{id} 不再带 - {label}（v2.2 优化 5）。"""

    def test_title_uses_id_only(self):
        content = render_dispatch("test", _ctx("test"))
        # 标题必须是 ## 任务：dev.backend:test 形式（不带 label）
        self.assertIn("## 任务：dev.backend:test", content)
        # 但 ctx 里的 label 不应出现在标题位置
        self.assertNotIn("## 任务：dev.backend:test - 测试阶段", content)


class TestLegacyReturnFormatRemoved(unittest.TestCase):
    """优化 3: 末尾旧"返回格式"段已删除，由 sub_agent_contract.yaml 取代。"""

    def test_no_legacy_return_format_section(self):
        for phase in ("test", "dev", "verify", "gate", "fix", "fix-gate", "simple", "final-gate"):
            content = render_dispatch(phase, _ctx(phase))
            # 旧"返回格式"4 字段段不再出现
            self.assertNotIn(
                "summary: 一句话总结",
                content,
                f"{phase}: 仍含旧'返回格式'段",
            )
            self.assertNotIn(
                "SUCCESS / FAILED",
                content,
                f"{phase}: 仍含旧 SUCCESS/FAILED 状态枚举",
            )

    def test_sub_agent_contract_still_injected(self):
        """验证 sub_agent_contract.yaml 块仍然存在（替代旧段）。"""
        content = render_dispatch("test", _ctx("test"))
        self.assertIn("Sub-agent 返回契约", content)
        self.assertIn("evidence_paths", content)
        self.assertIn("report_path", content)


class TestDispatchSizeReduction(unittest.TestCase):
    """每个 dispatch 大小应比 v2.1 显著下降。"""

    def test_dispatch_size_reasonable(self):
        """每个 dispatch 应 < 200 行（v2.1 平均 165-186）。"""
        # 注意：WITH_ENV phase 仍含 env.hooks；WITHOUT_ENV 应更小
        for phase in ("test", "dev", "verify", "gate", "fix", "fix-gate", "simple", "final-gate"):
            content = render_dispatch(phase, _ctx(phase))
            lines = content.count("\n")
            self.assertLess(
                lines, 250,
                f"{phase}: dispatch {lines} 行超过 250 行上限",
            )


if __name__ == "__main__":
    unittest.main()