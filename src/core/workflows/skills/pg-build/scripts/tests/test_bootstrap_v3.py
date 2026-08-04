#!/usr/bin/env python3
"""v3 bootstrap 测试。

覆盖：
- manifest.tracks[].enabled=true/false 派发逻辑
- 旧 manifest 缺 enabled 字段时默认禁用（warning）
- e2e / scenario track 在 manifest 中的 type 字段被识别
- target_module 被正确传递到 track_configs
"""

import importlib.util
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


def _write_yaml(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)


def _load_bootstrap():
    """延迟加载 bootstrap.py, 注入必要的 PG_PROJECT_ROOT."""
    return _load_module("bootstrap", os.path.join(_SCRIPTS, "bootstrap.py"))


class TestBootstrapManifestEnabled(unittest.TestCase):
    """v3: manifest.tracks[].enabled 严格控制派发。

    通过环境变量 PG_PROJECT_ROOT 引导 bootstrap.find_project_root
    找到测试目录的 .pg/project.yaml。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.change = "test-bootstrap-v3"
        # 写测试用 project.yaml
        pg_dir = os.path.join(self.tmpdir, ".pg")
        os.makedirs(pg_dir, exist_ok=True)
        _write_yaml(os.path.join(pg_dir, "project.yaml"), {
            "stages": [],
            "tracks": {},
        })
        # 写 change 目录与 manifest
        os.makedirs(os.path.join(self.tmpdir, ".pg", "changes", self.change))

        # 设置环境变量，让 find_project_root 返回 tmpdir
        self._old_env = os.environ.get("PG_PROJECT_ROOT")
        os.environ["PG_PROJECT_ROOT"] = self.tmpdir

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        if self._old_env is not None:
            os.environ["PG_PROJECT_ROOT"] = self._old_env
        else:
            os.environ.pop("PG_PROJECT_ROOT", None)

    def _write_manifest(self, manifest: dict) -> None:
        _write_yaml(
            os.path.join(self.tmpdir, ".pg", "changes", self.change, "execution-manifest.yaml"),
            manifest,
        )

    def _detect(self):
        """调用 _detect_pipeline_config_from_disk."""
        bootstrap = _load_bootstrap()
        return bootstrap._detect_pipeline_config_from_disk(self.change)

    def test_enabled_true_included(self):
        """enabled=true 的 track 进入 pipeline_order。"""
        self._write_manifest({
            "schema_version": "2026-06-30",
            "change": self.change,
            "stages": [{
                "name": "dev",
                "environment": "dev-local",
                "tracks": [
                    {"id": "backend", "type": "standard", "enabled": True,
                     "phase_prompts": {"test": {"tasks_md_section": "1. dev.backend:test"}}},
                ],
            }],
        })
        cfg = self._detect()
        self.assertIn("dev.backend", cfg["pipeline_order"])

    def test_enabled_false_excluded(self):
        """enabled=false 的 track 不进入 pipeline_order。"""
        self._write_manifest({
            "schema_version": "2026-06-30",
            "change": self.change,
            "stages": [{
                "name": "dev",
                "environment": "dev-local",
                "tracks": [
                    {"id": "backend", "type": "standard", "enabled": True,
                     "phase_prompts": {"test": {"tasks_md_section": "1. dev.backend:test"}}},
                    {"id": "frontend-e2e", "type": "e2e", "enabled": False,
                     "target_module": "frontend"},
                ],
            }],
        })
        cfg = self._detect()
        self.assertIn("dev.backend", cfg["pipeline_order"])
        self.assertNotIn("dev.frontend-e2e", cfg["pipeline_order"])

    def test_legacy_manifest_default_disable(self):
        """v3 安全策略：旧 manifest 缺 enabled 字段时默认禁用。"""
        self._write_manifest({
            "schema_version": "2026-06-30",
            "change": self.change,
            "stages": [{
                "name": "dev",
                "environment": "dev-local",
                "tracks": [
                    {"id": "backend", "type": "standard",
                     "phase_prompts": {"test": {"tasks_md_section": "1. dev.backend:test"}}},
                ],
            }],
        })
        import io
        from contextlib import redirect_stderr
        err = io.StringIO()
        with redirect_stderr(err):
            cfg = self._detect()
        # enabled 字段缺失 → 默认禁用
        self.assertNotIn("dev.backend", cfg["pipeline_order"])
        # 警告信息应出现
        self.assertIn("WARN", err.getvalue())
        self.assertIn("enabled", err.getvalue())

    def test_e2e_target_module_propagated(self):
        """e2e track 的 target_module 被传递到 track_configs。"""
        self._write_manifest({
            "schema_version": "2026-06-30",
            "change": self.change,
            "stages": [{
                "name": "real-integration",
                "environment": "dev-local",
                "tracks": [
                    {"id": "frontend-e2e", "type": "e2e", "enabled": True,
                     "target_module": "frontend"},
                ],
            }],
        })
        cfg = self._detect()
        self.assertIn("real-integration.frontend-e2e", cfg["pipeline_order"])
        tc = cfg["track_configs"]["real-integration.frontend-e2e"]
        self.assertEqual(tc.get("target_module"), "frontend")
        self.assertEqual(tc.get("type"), "e2e")


if __name__ == "__main__":
    unittest.main()
