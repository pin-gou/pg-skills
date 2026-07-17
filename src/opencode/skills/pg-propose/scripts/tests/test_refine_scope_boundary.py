#!/usr/bin/env python3
"""v4.1 Scope Boundary 校验测试.

覆盖 pg-auto-refine-check.py 的 _check_decision_target_scope 函数:
  - 合规的产物路径（含四类）应通过
  - 业务代码路径应被标记为越界
  - 含路径分隔符的相对路径解析
  - 绝对路径解析
  - 跨平台路径处理
"""
import importlib.util
import os
import sys
import tempfile
import unittest

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _SCRIPTS)


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


auto_refine_check = _load_module(
    "pg_auto_refine_check",
    os.path.join(_SCRIPTS, "pg-auto-refine-check.py"),
)
_check_decision_target_scope = auto_refine_check._check_decision_target_scope
_resolve_target_to_abs = auto_refine_check._resolve_target_to_abs


def _make_change_root() -> str:
    return tempfile.mkdtemp(prefix="pg_change_")


class TestScopeBoundaryProductFiles(unittest.TestCase):
    """合规的产物路径（含产物文件名 + 内联章节定位）应通过"""

    def setUp(self):
        self.change_root = _make_change_root()

    def test_bare_product_filename_passes(self):
        content = """
- [ ] **fix-something**
  - 目标：tasks.md
  - 推荐动作：补充任务
"""
        self.assertEqual(_check_decision_target_scope(content, self.change_root), [])

    def test_product_with_chapter_locator_passes(self):
        content = """
- [ ] **fix**
  - 目标：`tasks.md` 第 11.1 章节
  - 推荐动作：在 dev.agent:test 加测试
"""
        self.assertEqual(_check_decision_target_scope(content, self.change_root), [])

    def test_design_with_verification_passes(self):
        content = """
- [ ] **verify**
  - 目标：`design.md` V-backend-3 验证项
"""
        self.assertEqual(_check_decision_target_scope(content, self.change_root), [])

    def test_review_notes_passes(self):
        content = """
- [ ] **skip**
  - 目标：`review-notes.md` 第 1 条
"""
        self.assertEqual(_check_decision_target_scope(content, self.change_root), [])


class TestScopeBoundaryViolations(unittest.TestCase):
    """业务代码 / 配置文件路径应被标记为越界"""

    def setUp(self):
        self.change_root = _make_change_root()

    def test_relative_business_code_path_violates(self):
        content = """
- [ ] **bug**
  - 目标：.pg/hooks/env-dev-local-clean.sh
  - 推荐动作：改 step [3/3]
"""
        violations = _check_decision_target_scope(content, self.change_root)
        self.assertEqual(len(violations), 1)
        self.assertIn(".pg/hooks/env-dev-local-clean.sh", violations[0])

    def test_go_source_file_violates(self):
        content = """
- [ ] **fix**
  - 目标：webvirt-agent/internal/libvirt/network_manager.go
"""
        violations = _check_decision_target_scope(content, self.change_root)
        self.assertEqual(len(violations), 1)
        self.assertIn("network_manager.go", violations[0])

    def test_java_source_file_violates(self):
        content = """
- [ ] **fix**
  - 目标：`webvirt-backend/.../InstanceService.java`
"""
        violations = _check_decision_target_scope(content, self.change_root)
        self.assertEqual(len(violations), 1)

    def test_absolute_business_path_violates(self):
        content = """
- [ ] **fix**
  - 目标：/etc/passwd
"""
        violations = _check_decision_target_scope(content, self.change_root)
        self.assertEqual(len(violations), 1)


class TestResolveTargetToAbs(unittest.TestCase):
    """_resolve_target_to_abs 函数路径解析"""

    def setUp(self):
        self.change_root = _make_change_root()
        self.repo_root = tempfile.mkdtemp(prefix="pg_repo_")

    def test_absolute_path_passthrough(self):
        self.assertEqual(
            _resolve_target_to_abs("/etc/hosts", self.change_root, self.repo_root),
            "/etc/hosts",
        )

    def test_relative_path_against_repo_root(self):
        result = _resolve_target_to_abs(
            "src/foo.go", self.change_root, self.repo_root
        )
        self.assertEqual(result, os.path.join(self.repo_root, "src/foo.go"))

    def test_product_filename_resolves_under_change_root(self):
        result = _resolve_target_to_abs(
            "tasks.md", self.change_root, self.repo_root
        )
        self.assertEqual(result, os.path.join(self.change_root, "tasks.md"))


if __name__ == "__main__":
    unittest.main()