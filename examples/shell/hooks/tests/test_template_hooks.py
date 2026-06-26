"""test_template_hooks.py — 验证 5 个 hook 模板与 lib/common.sh SSOT 的一致性.

测试覆盖:
  1. lib/common.sh 存在且含 pg_resolve_paths + 三个 skill 路由
  2. 5 个模板都条件 source lib/common.sh + 调 pg_resolve_paths
  3. 条件 source 用 [[ -f ]] 守护 (硬依赖会让 lib 缺失的项目崩)
  4. 所有模板 bash 语法正确
  5. SSOT 三处一致: lib/common.sh 的 pg_resolve_paths 路由表与运行时 helper

跑法: python3 .pg/skills/examples/shell/hooks/tests/test_template_hooks.py
"""
import re
import subprocess
import sys
import unittest
from pathlib import Path


HOOKS_DIR = Path(__file__).parent.parent
LIB_COMMON = HOOKS_DIR / "lib" / "common.sh"
TEMPLATES = [
    "role-start.sh",
    "role-stop.sh",
    "role-logs.sh",
    "env-prepare.sh",
    "env-clean.sh",
]


class TestLibCommon(unittest.TestCase):
    """lib/common.sh SSOT 完整性."""

    def test_lib_common_exists(self):
        self.assertTrue(
            LIB_COMMON.is_file(),
            f"{LIB_COMMON} 不存在, 需先创建 SSOT",
        )

    def test_lib_common_has_pg_resolve_paths(self):
        content = LIB_COMMON.read_text(encoding="utf-8")
        self.assertIn(
            "pg_resolve_paths()",
            content,
            "lib/common.sh 缺 pg_resolve_paths 函数",
        )

    def test_lib_common_has_all_skill_routes(self):
        """三个 skill 路由必须都存在 (与运行时 helper 三处一致)."""
        content = LIB_COMMON.read_text(encoding="utf-8")
        for skill in ("pg-build", "pg-regression", "pg-fix-issue"):
            self.assertIn(
                skill, content,
                f"lib/common.sh 缺 {skill} 路由, "
                f"运行时 helper (pg-invoke-hook.py:pg_log_dir_for_skill, "
                f"pg-pipeline-runner.py:_pg_log_dir_for_skill) 会与本 SSOT 分叉",
            )

    def test_lib_common_has_per_skill_paths(self):
        """三个 skill 各自的目录前缀必须存在."""
        content = LIB_COMMON.read_text(encoding="utf-8")
        self.assertIn(".pg/changes", content, "缺 .pg/changes 路径 (pg-build)")
        self.assertIn(".pg/regression", content, "缺 .pg/regression 路径 (pg-regression)")
        self.assertIn(".pg/fix-issue", content, "缺 .pg/fix-issue 路径 (pg-fix-issue)")

    def test_lib_common_bash_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(LIB_COMMON)],
            capture_output=True, text=True,
        )
        self.assertEqual(
            result.returncode, 0,
            f"lib/common.sh bash 语法错: {result.stderr}",
        )


class TestTemplatesConditionalSource(unittest.TestCase):
    """5 个模板都条件 source lib/common.sh + 调 pg_resolve_paths."""

    def test_all_templates_exist(self):
        for tpl in TEMPLATES:
            path = HOOKS_DIR / tpl
            self.assertTrue(path.is_file(), f"模板 {tpl} 不存在")

    def test_all_templates_reference_lib_common(self):
        for tpl in TEMPLATES:
            content = (HOOKS_DIR / tpl).read_text(encoding="utf-8")
            self.assertIn(
                "lib/common.sh", content,
                f"{tpl} 缺 lib/common.sh 引用",
            )

    def test_all_templates_call_pg_resolve_paths(self):
        for tpl in TEMPLATES:
            content = (HOOKS_DIR / tpl).read_text(encoding="utf-8")
            self.assertIn(
                "pg_resolve_paths", content,
                f"{tpl} 缺 pg_resolve_paths 调用",
            )

    def test_conditional_source_uses_file_test(self):
        """条件 source 必须用 [[ -f ]] 守护, 不要硬依赖 (用户可能手工复制模板没带 lib)."""
        for tpl in TEMPLATES:
            content = (HOOKS_DIR / tpl).read_text(encoding="utf-8")
            pattern = r"if\s+\[\[\s+-f\s+[^\]]*lib/common\.sh"
            self.assertRegex(
                content, pattern,
                f"{tpl} 缺 [[ -f lib/common.sh ]] 守护, "
                f"硬依赖会让手工复制的项目崩",
            )

    def test_templates_bash_syntax(self):
        for tpl in TEMPLATES:
            result = subprocess.run(
                ["bash", "-n", str(HOOKS_DIR / tpl)],
                capture_output=True, text=True,
            )
            self.assertEqual(
                result.returncode, 0,
                f"{tpl} bash 语法错: {result.stderr}",
            )


class TestSSOTSyncMarker(unittest.TestCase):
    """本仓库 .pg/hooks/lib/common.sh 必须含 SSOT 同步标记 (项目本地副本约定)."""

    def test_project_common_has_ssot_marker(self):
        project_common = Path(__file__).parent.parent.parent.parent.parent.parent / "hooks" / "lib" / "common.sh"
        if not project_common.is_file():
            self.skipTest(f"项目本地副本 {project_common} 不存在, 跳过")
        content = project_common.read_text(encoding="utf-8")
        self.assertIn(
            "synced from", content,
            f"{project_common} 缺 SSOT 同步标记, "
            f"加顶部注释: '# >>> synced from .pg/skills/examples/shell/hooks/lib/common.sh (SSOT) <<<'",
        )


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
