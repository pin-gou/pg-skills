"""v2.6: profile_loader 单测。

覆盖：
- YAML profile 加载
- 优先级解析（用户显式 > language 自动 > default 兜底）
- Union 合并（weight=max, threshold=min）
- inherit 链展开（仅用于 checks，不稀释 threshold）
- Markdown 规则读取
- compute_review_score & decide_review_disposition
"""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.profile_loader import (
    Profile,
    CheckConfig,
    DEFAULT_PROFILE_NAME,
    LANGUAGE_PROFILE_MAP,
    resolve_profile_names,
    load_effective_profile,
    resolve_profile_for_track,
    load_markdown_rule,
    compute_review_score,
    decide_review_disposition,
    profile_index_path,
    profile_dir,
)


def _write_yaml(project_root: str, content: str) -> None:
    """写 .pg/code-review/code-review.yaml。"""
    path = profile_index_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _write_rule(project_root: str, profile_name: str, check_name: str, content: str) -> None:
    """写 .pg/code-review/<profile>/<check>.md。"""
    base = profile_dir(project_root) / profile_name
    base.mkdir(parents=True, exist_ok=True)
    with open(base / f"{check_name}.md", "w", encoding="utf-8") as f:
        f.write(content)


class TestConstants(unittest.TestCase):
    def test_default_name(self):
        self.assertEqual(DEFAULT_PROFILE_NAME, "default")

    def test_language_map_java(self):
        self.assertEqual(LANGUAGE_PROFILE_MAP["java"], "java-spring")
        self.assertEqual(LANGUAGE_PROFILE_MAP["kotlin"], "java-spring")

    def test_language_map_typescript(self):
        self.assertEqual(LANGUAGE_PROFILE_MAP["typescript"], "vue3")
        self.assertEqual(LANGUAGE_PROFILE_MAP["vue"], "vue3")

    def test_language_map_go(self):
        self.assertEqual(LANGUAGE_PROFILE_MAP["go"], "go")
        self.assertEqual(LANGUAGE_PROFILE_MAP["golang"], "go")


class TestResolveProfileNames(unittest.TestCase):
    """优先级解析。"""

    def test_explicit_profiles_win(self):
        # 显式 profiles 优先于 language
        self.assertEqual(
            resolve_profile_names(("security",), "", ("java",)),
            ["security"],
        )

    def test_explicit_multiple(self):
        self.assertEqual(
            resolve_profile_names(("security", "java-spring"), "", ("java",)),
            ["security", "java-spring"],
        )

    def test_legacy_single(self):
        # legacy 字段第二优先
        self.assertEqual(
            resolve_profile_names((), "security", ("java",)),
            ["security"],
        )

    def test_language_dispatch(self):
        self.assertEqual(
            resolve_profile_names((), "", ("java",)),
            ["java-spring"],
        )

    def test_language_multiple_unique(self):
        # java + go → java-spring + go（去重保序）
        self.assertEqual(
            resolve_profile_names((), "", ("java", "go")),
            ["java-spring", "go"],
        )

    def test_language_unknown_falls_back_to_default(self):
        self.assertEqual(
            resolve_profile_names((), "", ("cobol",)),
            ["default"],
        )

    def test_total_fallback(self):
        # 无配置 → default
        self.assertEqual(
            resolve_profile_names((), "", ()),
            ["default"],
        )


class TestLoadEffectiveProfile(unittest.TestCase):
    """Union 合并语义。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _write_yaml(self.tmp, textwrap.dedent("""\
            version: 1
            profiles:
              default:
                language: any
                checks:
                  design_alignment:    { enabled: true, weight: 30, doc: design_alignment }
                  scope_creep:         { enabled: true, weight: 25, doc: scope_creep }
                pass_threshold: 80
                escalate_threshold: 60
              security:
                language: any
                checks:
                  secret_leak: { enabled: true, weight: 25, doc: secret_leak }
                  auth_bypass: { enabled: true, weight: 20, doc: auth_bypass }
                pass_threshold: 90
                escalate_threshold: 70
              java-spring:
                inherit: default
                language: java
                checks:
                  pattern_consistency: { enabled: true, weight: 20, doc: pattern_consistency }
                pass_threshold: 85
        """))

    def test_default_only(self):
        p = load_effective_profile(self.tmp, ["default"])
        self.assertIn("design_alignment", p.check_names())
        self.assertEqual(p.pass_threshold, 80)

    def test_union_two_profiles(self):
        p = load_effective_profile(self.tmp, ["security", "java-spring"])
        # Union: design_alignment (from default via inherit) + scope_creep + secret_leak + auth_bypass + pattern_consistency
        names = set(p.check_names())
        self.assertIn("design_alignment", names)
        self.assertIn("scope_creep", names)
        self.assertIn("secret_leak", names)
        self.assertIn("auth_bypass", names)
        self.assertIn("pattern_consistency", names)
        # threshold 不被 default 稀释
        self.assertEqual(p.pass_threshold, 85)  # min(90, 85)

    def test_union_weight_max(self):
        # pattern_consistency: default 不定义，java-spring 定义 weight=20
        # scope_creep: default weight=25，security 不定义
        # secret_leak: security weight=25
        p = load_effective_profile(self.tmp, ["security", "java-spring"])
        pc = p.get_check("pattern_consistency")
        self.assertEqual(pc.weight, 20)
        sc = p.get_check("scope_creep")
        self.assertEqual(sc.weight, 25)

    def test_union_enabled_or(self):
        """如果 default 把某项 enabled=false，security 重新 enabled=true → 应 enabled。"""
        _write_yaml(self.tmp, textwrap.dedent("""\
            version: 1
            profiles:
              base:
                checks:
                  check_x: { enabled: false, weight: 10 }
                pass_threshold: 80
              extra:
                checks:
                  check_x: { enabled: true, weight: 10 }
                pass_threshold: 80
        """))
        p = load_effective_profile(self.tmp, ["base", "extra"])
        cx = p.get_check("check_x")
        self.assertTrue(cx.enabled)

    def test_threshold_min(self):
        """多个 profile 的 threshold 取 min（更严格）。"""
        _write_yaml(self.tmp, textwrap.dedent("""\
            version: 1
            profiles:
              loose:
                checks:
                  a: { enabled: true, weight: 10 }
                pass_threshold: 70
              strict:
                checks:
                  a: { enabled: true, weight: 10 }
                pass_threshold: 90
        """))
        p = load_effective_profile(self.tmp, ["loose", "strict"])
        self.assertEqual(p.pass_threshold, 70)

    def test_inherit_chain_does_not_dilute_threshold(self):
        """java-spring inherit default，但 threshold 不被 default (80) 稀释到 80。

        显式 profile 是 java-spring (85) + security (90)，期望 min = 85。
        """
        p = load_effective_profile(self.tmp, ["security", "java-spring"])
        # 安全 profile 90，java-spring 85 → 期望 min(90, 85) = 85
        # 不应是 min(90, 85, 80) = 80（default inherit 不参与 threshold）
        self.assertEqual(p.pass_threshold, 85)

    def test_single_profile_inheritance(self):
        """单 profile 含 inherit → checks 应展开，但 threshold 用 profile 自身。"""
        p = load_effective_profile(self.tmp, ["java-spring"])
        self.assertEqual(p.pass_threshold, 85)
        self.assertIn("design_alignment", p.check_names())  # 来自 default inherit

    def test_unknown_profile_returns_empty(self):
        """找不到的 profile 返回空骨架，不报错。"""
        p = load_effective_profile(self.tmp, ["does_not_exist"])
        self.assertEqual(p.name, "does_not_exist")
        self.assertEqual(p.checks, ())


class TestResolveForTrack(unittest.TestCase):
    """resolve_profile_for_track 端到端。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _write_yaml(self.tmp, textwrap.dedent("""\
            version: 1
            profiles:
              default:
                checks:
                  design_alignment: { enabled: true, weight: 30 }
                  scope_creep: { enabled: true, weight: 25 }
                pass_threshold: 80
              java-spring:
                inherit: default
                checks:
                  pattern_consistency: { enabled: true, weight: 20 }
                pass_threshold: 85
              go:
                inherit: default
                checks:
                  error_wrapping: { enabled: true, weight: 20 }
                pass_threshold: 85
              security:
                checks:
                  secret_leak: { enabled: true, weight: 25 }
                pass_threshold: 90
        """))

    def test_java_track_auto_dispatches_java_spring(self):
        p = resolve_profile_for_track(self.tmp, (), "", ("java",))
        self.assertIn("design_alignment", p.check_names())
        self.assertIn("pattern_consistency", p.check_names())

    def test_go_track_auto_dispatches_go(self):
        p = resolve_profile_for_track(self.tmp, (), "", ("go",))
        self.assertIn("error_wrapping", p.check_names())

    def test_unknown_language_falls_back_to_default(self):
        p = resolve_profile_for_track(self.tmp, (), "", ("cobol",))
        self.assertEqual(p.name, "default")

    def test_explicit_profiles_override_language(self):
        """security 显式指定时，不走 java 自动派发。"""
        p = resolve_profile_for_track(
            self.tmp, ("security", "java-spring"), "", ("java",),
        )
        # security 在前 = 高优先级
        names = set(p.check_names())
        self.assertIn("secret_leak", names)
        self.assertIn("pattern_consistency", names)
        # threshold 取 security (90) + java-spring (85) 的 min = 85
        self.assertEqual(p.pass_threshold, 85)


class TestMarkdownRule(unittest.TestCase):
    """markdown 规则读取。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _write_yaml(self.tmp, textwrap.dedent("""\
            version: 1
            profiles:
              default:
                checks:
                  design_alignment: { enabled: true, weight: 30, doc: design_alignment }
        """))

    def test_load_existing_rule(self):
        _write_rule(self.tmp, "default", "design_alignment", "# design 规则")
        content = load_markdown_rule(self.tmp, "default", "design_alignment")
        self.assertEqual(content, "# design 规则")

    def test_load_missing_rule(self):
        content = load_markdown_rule(self.tmp, "default", "nonexistent")
        self.assertEqual(content, "")

    def test_doc_field_alternative_filename(self):
        """如果 doc 字段指向不同的文件名，应尝试那个文件。"""
        _write_rule(self.tmp, "default", "alt_name", "# alt")
        _write_yaml(self.tmp, textwrap.dedent("""\
            version: 1
            profiles:
              default:
                checks:
                  check_a: { enabled: true, weight: 30, doc: alt_name }
        """))
        content = load_markdown_rule(self.tmp, "default", "check_a")
        self.assertEqual(content, "# alt")


class TestComputeReviewScore(unittest.TestCase):
    """review_score 计算。"""

    def test_all_pass(self):
        p = Profile(
            name="x",
            checks=(
                ("a", CheckConfig(True, 30)),
                ("b", CheckConfig(True, 20)),
            ),
        )
        self.assertEqual(compute_review_score(p, {"a": True, "b": True}), 100)

    def test_all_fail(self):
        p = Profile(
            name="x",
            checks=(
                ("a", CheckConfig(True, 30)),
                ("b", CheckConfig(True, 20)),
            ),
        )
        self.assertEqual(compute_review_score(p, {"a": False, "b": False}), 0)

    def test_partial_pass(self):
        p = Profile(
            name="x",
            checks=(
                ("a", CheckConfig(True, 30)),
                ("b", CheckConfig(True, 20)),
            ),
        )
        # weight pass 30 of 50
        score = compute_review_score(p, {"a": True, "b": False})
        self.assertEqual(score, 60)

    def test_disabled_excluded(self):
        p = Profile(
            name="x",
            checks=(
                ("a", CheckConfig(True, 30)),
                ("b", CheckConfig(False, 20)),  # disabled
            ),
        )
        # 只 a 计分，a 通过 → 100
        self.assertEqual(compute_review_score(p, {"a": True, "b": False}), 100)

    def test_missing_results_treated_as_fail(self):
        p = Profile(
            name="x",
            checks=(
                ("a", CheckConfig(True, 30)),
                ("b", CheckConfig(True, 20)),
            ),
        )
        # b 缺结果 → False
        self.assertEqual(compute_review_score(p, {"a": True}), 60)

    def test_empty_profile(self):
        p = Profile(name="empty")
        self.assertEqual(compute_review_score(p, {}), 100)


class TestDecideReviewDisposition(unittest.TestCase):
    def test_completed_above_pass_threshold(self):
        p = Profile(name="x", pass_threshold=80)
        self.assertEqual(decide_review_disposition(p, 80), "completed")
        self.assertEqual(decide_review_disposition(p, 100), "completed")

    def test_escalate_below_pass_threshold(self):
        p = Profile(name="x", pass_threshold=80)
        self.assertEqual(decide_review_disposition(p, 79), "escalate")
        self.assertEqual(decide_review_disposition(p, 0), "escalate")


if __name__ == "__main__":
    unittest.main()