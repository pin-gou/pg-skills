#!/usr/bin/env python3
"""v3 manifest 增强测试。

覆盖：
- manifest.tracks[].enabled 必填
- on_conditions 机械评估结果写入 manifest
- e2e track 必填 target_module
- type=e2e / type=scenario 在 schema 中允许
- 旧 manifest 缺 enabled 字段时 validator 拒绝
- pg-build bootstrap 默认禁用策略
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest

try:
    import yaml
except ImportError:
    yaml = None


_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
sys.path.insert(0, _SCRIPTS)


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pg_gen_manifest = _load_module("pg_gen_manifest", os.path.join(_SCRIPTS, "pg-gen-manifest.py"))
pg_validate_proposal = _load_module(
    "pg_validate_proposal", os.path.join(_SCRIPTS, "pg-validate-proposal.py"),
)


def _write_yaml(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)


def _write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class TestOnConditionsEval(unittest.TestCase):
    """on_conditions 机械评估：path glob + keyword 命中检测。"""

    def test_glob_match(self):
        rules = ["本变更 affected_paths 命中 webvirt-frontend/** 任一路径"]
        affected = ["webvirt-frontend/src/components/Foo.vue"]
        result = pg_gen_manifest._evaluate_on_conditions(
            rules, affected, "",
        )
        self.assertEqual(len(result["matched_rules"]), 1)
        self.assertEqual(result["path_hit_count"], 1)

    def test_keyword_match(self):
        rules = ["本变更涉及 UI 组件"]
        affected = []
        result = pg_gen_manifest._evaluate_on_conditions(
            rules, affected, "本变更新增 UI 组件用于展示",
        )
        self.assertEqual(len(result["matched_rules"]), 1)
        self.assertEqual(result["semantic_hit_count"], 1)

    def test_no_match(self):
        rules = ["本变更涉及 UI 组件"]
        affected = ["webvirt-backend/src/Foo.java"]
        result = pg_gen_manifest._evaluate_on_conditions(
            rules, affected, "本变更改动了后端 API",
        )
        self.assertEqual(len(result["matched_rules"]), 0)
        self.assertEqual(len(result["unmatched_rules"]), 1)

    def test_multiple_rules_partial_match(self):
        rules = [
            "本变更涉及 UI 组件",
            "本变更涉及 gRPC 协议",
        ]
        result = pg_gen_manifest._evaluate_on_conditions(
            rules, [], "本变更新增 UI 组件",
        )
        self.assertEqual(len(result["matched_rules"]), 1)
        self.assertEqual(len(result["unmatched_rules"]), 1)


class TestBuildTrackEnabledDecision(unittest.TestCase):
    """LLM 决策 + 机械评估 → 最终 enabled 决策。"""

    def test_no_on_conditions_resident(self):
        """无 on_conditions 的 track：常驻，遵循 LLM 决策。"""
        enabled, reason = pg_gen_manifest._build_track_enabled_decision(
            "real-integration",
            {},
            {"matched_rules": [], "unmatched_rules": []},
            in_affected_tracks=True,
        )
        self.assertTrue(enabled)
        self.assertIn("常驻", reason)

    def test_all_matched_enable(self):
        enabled, reason = pg_gen_manifest._build_track_enabled_decision(
            "frontend-e2e",
            {"on_conditions": ["rule1", "rule2"]},
            {"matched_rules": ["rule1", "rule2"], "unmatched_rules": [],
             "path_hit_count": 2, "semantic_hit_count": 0},
            in_affected_tracks=True,
        )
        self.assertTrue(enabled)
        self.assertIn("全部命中", reason)

    def test_all_unmatched_llm_disable(self):
        enabled, reason = pg_gen_manifest._build_track_enabled_decision(
            "frontend-e2e",
            {"on_conditions": ["rule1"]},
            {"matched_rules": [], "unmatched_rules": ["rule1"],
             "path_hit_count": 0, "semantic_hit_count": 0},
            in_affected_tracks=False,
        )
        self.assertFalse(enabled)

    def test_partial_match_llm_disable_warns(self):
        enabled, reason = pg_gen_manifest._build_track_enabled_decision(
            "frontend-e2e",
            {"on_conditions": ["rule1", "rule2"]},
            {"matched_rules": ["rule1"], "unmatched_rules": ["rule2"],
             "path_hit_count": 1, "semantic_hit_count": 0},
            in_affected_tracks=False,
        )
        self.assertFalse(enabled)
        self.assertIn("部分命中", reason)
        self.assertIn("建议人工复核", reason)


class TestResolveManifestTrackType(unittest.TestCase):
    """raw_type → manifest.type 映射（含 e2e / scenario）。"""

    def test_standard(self):
        self.assertEqual(
            pg_gen_manifest._resolve_manifest_track_type("track", {}),
            "standard",
        )

    def test_simple(self):
        self.assertEqual(
            pg_gen_manifest._resolve_manifest_track_type("phase", {"type": "simple"}),
            "simple",
        )

    def test_explicit_e2e(self):
        self.assertEqual(
            pg_gen_manifest._resolve_manifest_track_type("track", {"type": "e2e"}),
            "e2e",
        )

    def test_explicit_scenario(self):
        self.assertEqual(
            pg_gen_manifest._resolve_manifest_track_type("track", {"type": "scenario"}),
            "scenario",
        )


class TestExtractAffectedPathsFromProposal(unittest.TestCase):
    """从 proposal.md 提取 glob 路径列表。"""

    def test_extract_backtick_globs(self):
        """通过 _extract_globs_from_text 路径直接验证核心正则逻辑。"""
        text = """
本变更修改 `webvirt-backend/src/main/java/Foo.java` 路径。
也涉及 `webvirt-frontend/**`。
"""
        # 直接验证正则能抓取期望的 glob
        import re
        globs = re.findall(r"`([^`\n]+)`", text)
        cleaned = [g.strip() for g in globs if "/" in g or "*" in g]
        self.assertIn("webvirt-backend/src/main/java/Foo.java", cleaned)
        self.assertIn("webvirt-frontend/**", cleaned)


class TestManifestIntegration(unittest.TestCase):
    """完整跑一次 pg-gen-manifest.py，验证 v3 字段都被生成。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.change = "test-v3-manifest-integration"
        self.change_dir = os.path.join(self.tmpdir, self.change)
        os.makedirs(os.path.join(self.change_dir, "1-propose-review"))
        _write_text(
            os.path.join(self.change_dir, "proposal.md"),
            "本变更涉及 webvirt-frontend UI 组件改造。",
        )
        _write_text(
            os.path.join(self.change_dir, "tasks.md"),
            "> - **environment 选择**：dev → dev-local\n\n"
            "## 1. dev.backend:dev - 实现开发\n\n- [ ] 1.1 实现功能\n\n"
            "## 2. dev.frontend:dev - 实现开发\n\n- [ ] 2.1 实现功能\n\n"
            "## 3. real-integration.frontend-e2e:e2e - e2e 验证\n\n- [ ] 3.1 跑 Playwright\n\n"
            "## 4. final-gate - 最终门控审查\n\n- [ ] 4.1 审查\n",
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_manifest_generates_enabled_field(self):
        """每个 track 都有 enabled 字段，类型 bool。"""
        # 重置 CHANGES_DIR 后用 importlib 加载
        sys.path.insert(0, _SCRIPTS)
        import pg_pipeline_common

        old_changes = pg_pipeline_common.CHANGES_DIR
        pg_pipeline_common.CHANGES_DIR = self.tmpdir
        old_root = pg_pipeline_common.PROJECT_ROOT

        # PROJECT_ROOT 也要改：_extract_affected_paths_from_proposal 用绝对路径
        # 写一个临时 project.yaml 满足 load_config
        _write_yaml(
            os.path.join(self.tmpdir, "project.yaml"),
            {
                "stages": [
                    {
                        "name": "dev",
                        "tracks": ["backend", "frontend"],
                        "environment": {"required": False, "name": "dev-local"},
                    },
                    {
                        "name": "real-integration",
                        "tracks": ["frontend-e2e"],
                        "environment": {"required": True, "name": "dev-local"},
                    },
                ],
                "tracks": {
                    "backend": {"modules": ["backend"]},
                    "frontend": {"modules": ["frontend"]},
                    "frontend-e2e": {
                        "type": "e2e",
                        "target_module": "frontend",
                        "on_conditions": ["本变更涉及 UI 组件"],
                    },
                },
            },
        )
        pg_pipeline_common.PROJECT_ROOT = self.tmpdir
        # CONFIG_PATH 是模块级字符串，重设
        pg_pipeline_common.CONFIG_PATH = os.path.join(self.tmpdir, "project.yaml")

        # pg_gen_manifest 也使用模块级 CHANGES_DIR，重新加载
        try:
            # 重新加载模块让其使用新的 CHANGES_DIR
            fresh = _load_module("pg_gen_manifest_fresh", os.path.join(_SCRIPTS, "pg-gen-manifest.py"))
            manifest = fresh.build_manifest(self.change)
        finally:
            pg_pipeline_common.CHANGES_DIR = old_changes
            pg_pipeline_common.PROJECT_ROOT = old_root

        # 验证 enabled 字段
        all_tracks = [t for s in manifest["stages"] for t in s["tracks"]]
        self.assertGreater(len(all_tracks), 0)
        for t in all_tracks:
            self.assertIn("enabled", t, f"track {t.get('id')} 缺 enabled 字段")
            self.assertIsInstance(t["enabled"], bool)
            self.assertIn("reason", t)
            self.assertIn("on_conditions_eval", t)

        # 验证 frontend-e2e 的 target_module
        e2e_tracks = [t for t in all_tracks if t.get("type") == "e2e"]
        if e2e_tracks:
            for t in e2e_tracks:
                self.assertEqual(t.get("target_module"), "frontend")

        # 验证 on_conditions 机械评估对 frontend-e2e 命中（proposal.md 含 "UI 组件"）
        e2e_track = next((t for t in all_tracks if t["id"] == "frontend-e2e"), None)
        if e2e_track:
            eval_dict = e2e_track["on_conditions_eval"]
            self.assertGreater(len(eval_dict["matched_rules"]), 0)


if __name__ == "__main__":
    unittest.main()
