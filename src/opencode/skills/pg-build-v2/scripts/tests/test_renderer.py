"""Render / Dispatch / Manifest 测试。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from template_engine.renderer import render_dispatch, render_dispatch_file, _get_templates_dir
from template_engine.manifest import read_manifest, get_pipeline_order_from_manifest, SUPPORTED_MANIFEST_VERSIONS
from pipeline.dispatch import build_ctx, build_action, build_final_gate_action
from pipeline.state import PipelineState, TrackState
from pipeline.events import PipelineAction


class TestRenderer(unittest.TestCase):
    """模板渲染测试。"""

    def test_render_test_phase(self):
        """test 模板渲染无报错。"""
        ctx = {
            "id": "dev.backend",
            "label": "后端测试",
            "_change": "my-change",
            "review_level": "standard",
            "modules": "['backend']",
            "max_fix_retries": 5,
            "fix_routing": "source",
            "module_details": "- module: backend",
            "stage_name": "dev",
            "test_key": "unit",
            "gate": "all_pass",
            "env_required": True,
            "env_name": "dev-local",
            "prepare_status": "ok",
            "prepare_log_path": "",
            "test_commands": "cd backend && mvn test",
            "module_roots": "[webvirt-backend]",
            "tasks_preformatted": "- [ ] 1.1 test",
            "tasks_validation": "all PASS",
        }
        result = render_dispatch("test", ctx)
        self.assertIn("任务", result)
        self.assertIn("TDD", result)
        self.assertIn("dev.backend", result)

    def test_render_verify_phase(self):
        ctx = {"id": "dev.backend", "_change": "x", "report_filename": "verify-report.md",
               "review_level": "", "modules": "", "max_fix_retries": 3, "fix_routing": "",
               "module_details": "", "stage_name": "", "test_key": "", "gate": "",
               "env_required": "", "env_name": "", "prepare_status": "", "prepare_log_path": "",
               "test_commands": "", "module_roots": "", "tasks_preformatted": "",
               "tasks_validation": ""}
        result = render_dispatch("verify", ctx)
        self.assertIn("V-*", result)

    def test_render_fix_phase(self):
        ctx = {"id": "backend", "_change": "x", "verify_report_path": "/tmp/verify.md",
               "fix_cycle": "1", "test_commands": "mvn test",
               "fix_report_filename": "fix-report.md", "review_level": "", "modules": "",
               "max_fix_retries": 3, "fix_routing": "", "module_details": "", "stage_name": "",
               "test_key": "", "gate": "", "env_required": "", "env_name": "",
               "prepare_status": "", "prepare_log_path": "", "module_roots": "",
               "tasks_preformatted": "", "tasks_validation": ""}
        result = render_dispatch("fix", ctx)
        self.assertIn("verify.md", result)
        self.assertIn("ESCALATE", result)
        self.assertIn("fix_cycle", result)

    def test_render_gate_phase(self):
        ctx = {"id": "backend", "_change": "x", "report_filename": "gate-report.md",
               "review_level": "", "modules": "", "max_fix_retries": 3, "fix_routing": "",
               "module_details": "", "stage_name": "", "test_key": "", "gate": "",
               "env_required": "", "env_name": "", "prepare_status": "", "prepare_log_path": "",
               "test_commands": "", "module_roots": "", "tasks_preformatted": "",
               "tasks_validation": ""}
        result = render_dispatch("gate", ctx)
        self.assertIn("G-*", result)

    def test_render_all_phases(self):
        """所有 8 个 phase 都可以渲染。"""
        phases = ["test", "dev", "verify", "gate", "fix", "fix-gate", "simple", "final-gate"]
        ctx = {"id": "x", "_change": "x", "review_level": "", "modules": "",
               "max_fix_retries": 3, "fix_routing": "", "module_details": "",
               "stage_name": "", "test_key": "", "gate": "", "env_required": "",
               "env_name": "", "prepare_status": "", "prepare_log_path": "",
               "test_commands": "", "module_roots": "", "tasks_preformatted": "",
               "tasks_validation": "",
               "verify_report_path": "", "fix_cycle": 1, "fix_report_filename": "",
               "gate_report_path": "", "gate_cycles": 1, "cycles_remaining": 1,
               "max_gate_fix_retries": 2,
               "track_timeout": "", "track_on_failure": "", "commands_normalized": "",
               "proposal_path": "", "tasks_path": "", "design_doc_paths": "",
               "report_paths": "", "report_filename": "", "label": ""}
        for phase in phases:
            with self.subTest(phase=phase):
                result = render_dispatch(phase, ctx)
                self.assertIn("任务", result) if phase != "simple" else self.assertNotEqual(result, "")

    def test_render_unknown_phase_raises(self):
        ctx = {"id": "x", "_change": "x"}
        with self.assertRaises(FileNotFoundError):
            render_dispatch("unknown-phase", ctx)

    def test_render_and_write_file(self):
        """render_dispatch_file 写入文件并返回路径。"""
        tmp = tempfile.mkdtemp()
        ctx = {"id": "dev.backend", "_change": "test-change", "review_level": "",
               "modules": "", "max_fix_retries": 3, "fix_routing": "", "module_details": "",
               "stage_name": "", "test_key": "", "gate": "", "env_required": "",
               "env_name": "", "prepare_status": "", "prepare_log_path": "",
               "test_commands": "", "module_roots": "", "tasks_preformatted": "",
               "tasks_validation": ""}
        filepath = render_dispatch_file(tmp, "backend", "test", ctx)
        self.assertTrue(os.path.isfile(filepath))
        self.assertIn("backend-test-dispatch.md", filepath)


class TestManifest(unittest.TestCase):
    """execution-manifest.yaml 读取测试。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        import yaml
        self.manifest = {
            "schema_version": "2026-06-30",
            "stages": [
                {"name": "dev", "tracks": [{"id": "backend"}, {"id": "frontend"}]},
            ],
            "final_gate": True,
        }
        with open(os.path.join(self.tmp, "execution-manifest.yaml"), "w") as f:
            yaml.dump(self.manifest, f)

    def test_read_manifest(self):
        data = read_manifest(self.tmp)
        self.assertEqual(data["schema_version"], "2026-06-30")
        self.assertTrue(data["final_gate"])

    def test_read_missing_manifest(self):
        with self.assertRaises(FileNotFoundError):
            read_manifest("/nonexistent")

    def test_get_pipeline_order(self):
        order = get_pipeline_order_from_manifest(self.tmp)
        self.assertIn("dev.backend", order)
        self.assertIn("dev.frontend", order)
        self.assertIn("final-gate", order)

    def test_unsupported_schema_version(self):
        import yaml
        with open(os.path.join(self.tmp, "execution-manifest.yaml"), "w") as f:
            yaml.dump({"schema_version": "2099-01-01"}, f)
        with self.assertRaises(ValueError):
            read_manifest(self.tmp)


class TestBuildCtx(unittest.TestCase):
    """dispatch 上下文构建测试。"""

    def test_build_ctx_has_required_fields(self):
        state = PipelineState(
            change="my-change",
            pipeline_order=("dev.backend",),
            tracks={"dev.backend": TrackState.create("dev.backend", modules=("backend",))},
        )
        ctx = build_ctx(state, "dev.backend", "test")
        self.assertEqual(ctx["_change"], "my-change")
        self.assertEqual(ctx["id"], "dev.backend")
        self.assertEqual(ctx["modules"], ["backend"])

    def test_build_ctx_lifecycle(self):
        """确保 build_ctx 在所有 phase 上不抛异常。"""
        state = PipelineState(change="x", pipeline_order=("backend",),
                              tracks={"backend": TrackState.create("backend")})
        for phase in ("test", "dev", "verify", "gate", "fix", "fix-gate", "simple"):
            with self.subTest(phase=phase):
                ctx = build_ctx(state, "backend", phase)
                self.assertIsNotNone(ctx)


class TestBuildAction(unittest.TestCase):
    """dispatch action 构建测试。"""

    def test_build_action(self):
        state = PipelineState(change="x", pipeline_order=("backend",))
        action = PipelineAction(kind="dispatch", track="backend", phase="test", cycle=1)
        tmp = tempfile.mkdtemp()
        result = build_action(state, action, tmp)
        self.assertEqual(result["action"], "dispatch")
        self.assertEqual(result["item"], "backend")
        self.assertEqual(result["sub"], "test")
        self.assertEqual(result["agent"], "pg-build/test")
        self.assertIn("dispatch_file", result)
        self.assertTrue(os.path.isfile(result["dispatch_file"]))

    def test_build_final_gate(self):
        state = PipelineState(change="x")
        tmp = tempfile.mkdtemp()
        result = build_final_gate_action(state, tmp)
        self.assertEqual(result["action"], "dispatch_final_gate")
        self.assertEqual(result["item"], "final-gate")


if __name__ == "__main__":
    unittest.main()