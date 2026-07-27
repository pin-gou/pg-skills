#!/usr/bin/env python3
"""v0.8.4: 已废弃 — test_refine_scope_boundary.py

原测试覆盖 pg-auto-refine-check.py 的 _check_decision_target_scope 函数。

v0.8.4 起，pg-propose-refine 流程已删除，pg-auto-refine-check.py 随 SKILL 目录一并删除。
本测试文件保留为占位符，避免 pytest 收集时 import 错误；测试类被标记为
unittest.skip，CI 不会运行。

历史背景：v4.1 引入 scope boundary 校验，v0.8.4 起被 pg-validate-proposal.py
的 scenario_yaml_referenced 规则替代（任务 body 引用 scenario-*.yaml 时 WARN）。
"""
import unittest


@unittest.skip("v0.8.4: pg-propose-refine / pg-auto-refine-check.py 已删除")
class TestRefineScopeBoundaryDeprecated(unittest.TestCase):
    """占位测试类，v0.8.4 起被 skip。"""

    def test_deprecated(self):
        self.fail("不应运行")


if __name__ == "__main__":
    unittest.main()
