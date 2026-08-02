#!/usr/bin/env python3
"""pg-list-phases.py 测试。

覆盖：
- 基础 manifest 派生（standard track 5 phases）
- 多 stage / 多 track 派生
- final-gate 派生
- enabled=false track 被过滤
- snapshot 缺失时的容错
- snapshot + progress 应用 status
- sub-pipeline 检测（fix-cycle / gate-fix-cycle）
- manifest 缺失时的错误输出
- section_key 解析（正常/异常）

不依赖真实网络/磁盘副作用，全部在 tmpdir 中模拟。
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

try:
    import yaml
except ImportError:
    yaml = None


_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
_SCRIPT_PATH = os.path.join(_SCRIPTS, "pg-list-phases.py")
sys.path.insert(0, _SCRIPTS)


def _load_module():
    spec = importlib.util.spec_from_file_location("pg_list_phases", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_yaml(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)


def _write_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.change = "test-list-phases"
        pg_dir = os.path.join(self.tmpdir, ".pg")
        os.makedirs(os.path.join(pg_dir, "changes", self.change), exist_ok=True)
        _write_yaml(os.path.join(pg_dir, "project.yaml"), {
            "stages": [], "tracks": {},
        })
        self._old_env = os.environ.get("PG_PROJECT_ROOT")
        os.environ["PG_PROJECT_ROOT"] = self.tmpdir
        self.mod = _load_module()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        if self._old_env is not None:
            os.environ["PG_PROJECT_ROOT"] = self._old_env
        else:
            os.environ.pop("PG_PROJECT_ROOT", None)

    def _write_manifest(self, manifest: dict) -> None:
        _write_yaml(
            os.path.join(self.tmpdir, ".pg", "changes", self.change,
                         "execution-manifest.yaml"),
            manifest,
        )

    def _write_snapshot(self, snapshot: dict) -> None:
        d = os.path.join(self.tmpdir, ".pg", "changes", self.change, "2-build")
        os.makedirs(d, exist_ok=True)
        _write_json(os.path.join(d, "pipeline.snapshot.json"), snapshot)

    def _run_cli(self, *args) -> dict:
        proc = subprocess.run(
            [sys.executable, _SCRIPT_PATH, self.change, *args],
            capture_output=True, text=True,
            env={**os.environ, "PG_PROJECT_ROOT": self.tmpdir},
        )
        self.assertEqual(proc.returncode, 0,
                         msg=f"CLI exit={proc.returncode}, stderr={proc.stderr}")
        return json.loads(proc.stdout)


class TestParseSectionKey(_Fixture):
    def test_standard_with_sub(self):
        r = self.mod._parse_section_key("1. dev.backend:test - dev 测试先行")
        self.assertEqual(r, {
            "stage": "dev", "track": "backend", "sub": "test",
            "label": "dev 测试先行",
        })

    def test_final_gate_no_sub(self):
        r = self.mod._parse_section_key("11. final-gate - 最终门控审查")
        self.assertEqual(r, {
            "stage": "final-gate", "track": "final-gate", "sub": None,
            "label": "最终门控审查",
        })

    def test_invalid_returns_none(self):
        self.assertIsNone(self.mod._parse_section_key("garbage"))


class TestBuildItemsFromManifest(_Fixture):
    def test_standard_5_phases(self):
        self._write_manifest({
            "schema_version": "2026-06-30",
            "change": self.change,
            "stages": [{
                "name": "dev",
                "environment": "dev-local",
                "tracks": [{
                    "id": "backend", "type": "standard", "enabled": True,
                    "phase_prompts": {
                        "test": {"tasks_md_section":
                                 "1. dev.backend:test - 测试"},
                        "dev": {"tasks_md_section":
                                "2. dev.backend:dev - 开发"},
                        "review": {"tasks_md_section":
                                   "3. dev.backend:review - 审查"},
                        "verify": {"tasks_md_section":
                                   "4. dev.backend:verify - 验证"},
                        "gate": {"tasks_md_section":
                                 "5. dev.backend:gate - 门控"},
                    },
                }],
            }],
            "final_gate": {"tasks_md_section":
                           "6. final-gate - 最终门控"},
        })
        items = self.mod._build_items_from_manifest(
            self.mod._load_manifest(
                os.path.join(self.tmpdir, ".pg", "changes", self.change)
            )
        )
        ids = [it["id"] for it in items]
        self.assertIn("dev.backend:test", ids)
        self.assertIn("dev.backend:dev", ids)
        self.assertIn("dev.backend:review", ids)
        self.assertIn("dev.backend:verify", ids)
        self.assertIn("dev.backend:gate", ids)
        self.assertIn("final-gate", ids)
        for it in items:
            self.assertEqual(it["status"], "pending")
        fg = [it for it in items if it["id"] == "final-gate"][0]
        self.assertEqual(fg["kind"], "final-gate")

    def test_enabled_false_filtered(self):
        self._write_manifest({
            "schema_version": "2026-06-30",
            "change": self.change,
            "stages": [{
                "name": "dev", "environment": "dev-local",
                "tracks": [
                    {"id": "backend", "type": "standard", "enabled": True,
                     "phase_prompts": {"test": {"tasks_md_section":
                                                "1. dev.backend:test - t"}}},
                    {"id": "frontend", "type": "standard", "enabled": False,
                     "phase_prompts": {"test": {"tasks_md_section":
                                                "2. dev.frontend:test - t"}}},
                ],
            }],
        })
        items = self.mod._build_items_from_manifest(
            self.mod._load_manifest(
                os.path.join(self.tmpdir, ".pg", "changes", self.change)
            )
        )
        ids = [it["id"] for it in items]
        self.assertIn("dev.backend:test", ids)
        self.assertNotIn("dev.frontend:test", ids)

    def test_scenario_track_sub(self):
        self._write_manifest({
            "schema_version": "2026-06-30",
            "change": self.change,
            "stages": [{
                "name": "int", "environment": "dev-local",
                "tracks": [{
                    "id": "scr", "type": "scenario", "enabled": True,
                    "scenario_yaml": "scenario-scr.yaml",
                    "phase_prompts": {
                        "scenario-execute": {"tasks_md_section":
                                             "1. int.scr:scenario-execute - 真机场景"},
                    },
                }],
            }],
        })
        items = self.mod._build_items_from_manifest(
            self.mod._load_manifest(
                os.path.join(self.tmpdir, ".pg", "changes", self.change)
            )
        )
        ids = [it["id"] for it in items]
        self.assertIn("int.scr:scenario-execute", ids)
        self.assertEqual(items[0]["track_type"], "scenario")


class TestCliBasic(_Fixture):
    def test_init_mode_outputs_items(self):
        self._write_manifest({
            "schema_version": "2026-06-30",
            "change": self.change,
            "stages": [{
                "name": "dev", "environment": "dev-local",
                "tracks": [{
                    "id": "backend", "type": "standard", "enabled": True,
                    "phase_prompts": {"test": {"tasks_md_section":
                                               "1. dev.backend:test - 测试"}},
                }],
            }],
        })
        out = self._run_cli()
        self.assertTrue(out["manifest_present"])
        self.assertEqual(len(out["items"]), 1)
        self.assertEqual(out["items"][0]["id"], "dev.backend:test")
        self.assertEqual(out["items"][0]["status"], "pending")
        self.assertEqual(out["sub_pipeline_items"], [])

    def test_manifest_missing_returns_error(self):
        proc = subprocess.run(
            [sys.executable, _SCRIPT_PATH, self.change],
            capture_output=True, text=True,
            env={**os.environ, "PG_PROJECT_ROOT": self.tmpdir},
        )
        self.assertEqual(proc.returncode, 1)
        out = json.loads(proc.stdout)
        self.assertIn("error", out)
        self.assertEqual(out["items"], [])


class TestDetectSubPipelines(_Fixture):
    def _write_minimal_manifest(self):
        self._write_manifest({
            "schema_version": "2026-06-30",
            "change": self.change,
            "stages": [{
                "name": "dev", "environment": "dev-local",
                "tracks": [{
                    "id": "backend", "type": "standard", "enabled": True,
                    "phase_prompts": {"test": {"tasks_md_section":
                                               "1. dev.backend:test - t"}},
                }],
            }],
        })

    def test_fix_cycle_detected(self):
        self._write_minimal_manifest()
        self._write_snapshot({
            "change": self.change,
            "status": "running",
            "current_track": "dev.backend",
            "current_phase": "fix",
            "current_sub_pipeline": {
                "pipeline_id": "dev.backend.fix-1",
                "parent_track": "dev.backend",
                "parent_phase": "verify",
                "cycle": 1,
                "kind": "fix-cycle",
                "phases": ["fix", "verify"],
                "current_index": 0,
                "status": "running",
            },
        })
        out = self._run_cli("--detect-sub-pipelines")
        self.assertEqual(len(out["sub_pipeline_items"]), 2)
        first = out["sub_pipeline_items"][0]
        self.assertEqual(first["phase"], "fix")
        self.assertEqual(first["status"], "in_progress")
        self.assertEqual(first["kind"], "fix-cycle")
        second = out["sub_pipeline_items"][1]
        self.assertEqual(second["phase"], "verify")
        self.assertEqual(second["status"], "pending")

    def test_no_sub_pipeline_returns_empty(self):
        self._write_minimal_manifest()
        self._write_snapshot({
            "change": self.change,
            "status": "running",
            "current_track": "dev.backend",
            "current_phase": "dev",
        })
        out = self._run_cli("--detect-sub-pipelines")
        self.assertEqual(out["sub_pipeline_items"], [])

    def test_snapshot_missing_graceful(self):
        self._write_minimal_manifest()
        out = self._run_cli("--detect-sub-pipelines")
        self.assertEqual(out["sub_pipeline_items"], [])


class TestApplyProgress(_Fixture):
    def test_current_phase_marked_in_progress(self):
        items = [
            {"id": "dev.backend:test", "track": "backend",
             "phase": "test", "status": "pending", "kind": "phase"},
            {"id": "dev.backend:dev", "track": "backend",
             "phase": "dev", "status": "pending", "kind": "phase"},
        ]
        snapshot = {
            "current_track": "dev.backend",
            "current_phase": "dev",
            "tracks": {
                "dev.backend": {
                    "status": "running",
                    "phases": {
                        "test": {"status": "completed"},
                        "dev": {"status": "running"},
                    },
                },
            },
        }
        self.mod._apply_progress(items, None, snapshot)
        self.assertEqual(items[0]["status"], "completed")
        self.assertEqual(items[1]["status"], "in_progress")

    def test_workflow_completed_marks_all(self):
        items = [
            {"id": "dev.backend:test", "track": "backend",
             "phase": "test", "status": "pending", "kind": "phase"},
            {"id": "final-gate", "track": "final-gate",
             "phase": "gate", "status": "pending", "kind": "final-gate"},
        ]
        snapshot = {"status": "completed"}
        self.mod._apply_progress(items, None, snapshot)
        self.assertEqual(items[0]["status"], "completed")
        self.assertEqual(items[1]["status"], "completed")

    def test_no_progress_no_snapshot_noop(self):
        items = [{"id": "x", "track": "x", "phase": "test",
                  "status": "pending", "kind": "phase"}]
        self.mod._apply_progress(items, None, None)
        self.assertEqual(items[0]["status"], "pending")


class TestEndToEnd(_Fixture):
    def test_combined_flags(self):
        """--with-progress --detect-sub-pipelines 组合调用."""
        self._write_manifest({
            "schema_version": "2026-06-30",
            "change": self.change,
            "stages": [{
                "name": "dev", "environment": "dev-local",
                "tracks": [{
                    "id": "backend", "type": "standard", "enabled": True,
                    "phase_prompts": {
                        "test": {"tasks_md_section":
                                 "1. dev.backend:test - t"},
                        "dev": {"tasks_md_section":
                                "2. dev.backend:dev - d"},
                    },
                }],
            }],
        })
        self._write_snapshot({
            "current_track": "dev.backend",
            "current_phase": "dev",
            "current_sub_pipeline": {
                "parent_track": "dev.backend",
                "parent_phase": "verify",
                "cycle": 1,
                "kind": "fix-cycle",
                "phases": ["fix", "verify"],
                "current_index": 1,
            },
            "tracks": {
                "dev.backend": {
                    "status": "running",
                    "phases": {
                        "test": {"status": "completed"},
                        "dev": {"status": "running"},
                    },
                },
            },
        })
        out = self._run_cli("--with-progress", "--detect-sub-pipelines")
        items_by_id = {it["id"]: it for it in out["items"]}
        self.assertEqual(items_by_id["dev.backend:test"]["status"], "completed")
        self.assertEqual(items_by_id["dev.backend:dev"]["status"], "in_progress")
        self.assertEqual(len(out["sub_pipeline_items"]), 2)
        self.assertEqual(out["sub_pipeline_items"][0]["status"], "completed")
        self.assertEqual(out["sub_pipeline_items"][1]["status"], "in_progress")


if __name__ == "__main__":
    unittest.main()