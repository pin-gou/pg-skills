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
from pipeline.dispatch import build_ctx, build_action, build_final_gate_action, _set_project_root, _PROJECT_CONFIG_CACHE
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
               "review_level": "", "modules": "", "max_fix_retries": 3,
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
               "max_fix_retries": 3, "module_details": "", "stage_name": "",
               "test_key": "", "gate": "", "env_required": "", "env_name": "",
               "prepare_status": "", "prepare_log_path": "", "module_roots": "",
               "tasks_preformatted": "", "tasks_validation": ""}
        result = render_dispatch("fix", ctx)
        self.assertIn("verify.md", result)
        self.assertIn("ESCALATE", result)
        self.assertIn("fix_cycle", result)

    def test_render_gate_phase(self):
        ctx = {"id": "backend", "_change": "x", "report_filename": "gate-report.md",
               "review_level": "", "modules": "", "max_fix_retries": 3,
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
               "max_fix_retries": 3, "module_details": "",
               "stage_name": "", "test_key": "", "gate": "", "env_required": "",
               "env_name": "", "prepare_status": "", "prepare_log_path": "",
               "test_commands": "", "module_roots": "", "tasks_preformatted": "",
               "tasks_validation": "",
               "verify_report_path": "", "fix_cycle": 1, "fix_report_filename": "",
               "gate_report_path": "", "gate_cycles": 1, "cycles_remaining": 1,
               "max_gate_fix_retries": 2,
               "track_timeout": 1800, "commands_normalized": "",
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
               "modules": "", "max_fix_retries": 3, "module_details": "",
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

    def setUp(self):
        # 清除项目配置缓存（避免跨测试污染）
        _PROJECT_CONFIG_CACHE.clear()
        _set_project_root("")

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

    def test_build_ctx_lazy_enrichment(self):
        """旧快照（无富化字段）场景：build_ctx 惰性从 project.yaml 现场解析。"""
        import os, tempfile, yaml

        tmp = tempfile.mkdtemp()
        pg_dir = os.path.join(tmp, ".pg")
        os.makedirs(pg_dir)
        project_config = {
            "modules": {
                "backend": {
                    "root": "webvirt-backend",
                    "language": "java",
                    "build": "cd webvirt-backend && mvn clean install -DskipTests",
                    "lint": "cd webvirt-backend && mvn checkstyle:check",
                    "test": {"unit": "cd webvirt-backend && mvn test"},
                    "review_level": "security",
                },
            },
            "environments": {
                "dev-local": {
                    "roles": {
                        "backend": {
                            "instances": [
                                {"name": "backend-1", "host": "localhost", "port": 9080},
                            ],
                            "actions": {
                                "start": {
                                    "host": "localhost",
                                    "script": ".pg/hooks/role-backend-start.sh",
                                    "timeout_seconds": 300,
                                    "description": "Start backend service",
                                },
                                "stop": {
                                    "host": "localhost",
                                    "script": ".pg/hooks/role-backend-stop.sh",
                                    "timeout_seconds": 30,
                                },
                                "logs": {
                                    "host": "localhost",
                                    "script": ".pg/hooks/role-backend-logs.sh",
                                    "timeout_seconds": 30,
                                },
                            },
                        },
                    },
                },
            },
        }
        with open(os.path.join(pg_dir, "project.yaml"), "w") as f:
            yaml.dump(project_config, f)

        # 创建 change 目录和 design.md（用于验证 tasks_validation 的惰性解析）
        change_dir = os.path.join(tmp, ".pg", "changes", "test-change")
        os.makedirs(change_dir, exist_ok=True)
        design_md = os.path.join(change_dir, "design.md")
        with open(design_md, "w") as f:
            f.write("""## 设计

### dev backend Verification Criteria

| ID | 验证项 | 方法 | 预期结果 |
|-----|--------|------|---------|
| V-backend-1 | GET /documents 分页 | curl GET /api/.../documents | 200 |
| V-backend-2 | GET /documents/version | curl GET /api/.../documents/1 | 200 |

### dev frontend Verification Criteria

| ID | 验证项 | 方法 | 预期结果 |
|-----|--------|------|---------|
| V-frontend-1 | 双 Tab 显示 | 浏览器访问 | 显示两个标签 |
""")

        # 清空缓存，设置 tmp 为 project root
        _PROJECT_CONFIG_CACHE.clear()
        _set_project_root(tmp)

        # TrackState 没有富化字段（模拟旧快照）
        state = PipelineState(
            change="test-change",
            pipeline_order=("dev.backend",),
            tracks={
                "dev.backend": TrackState.create(
                    "dev.backend",
                    modules=("backend", "agent-proto"),
                ),
            },
            stage_env_map={"dev": "dev-local"},
        )
        ctx = build_ctx(state, "dev.backend", "test", change_root=change_dir)

        # 验证惰性解析结果
        self.assertIn("webvirt-backend", ctx["module_roots"],
                      "module_roots 应通过惰性解析填充")
        self.assertIn("module: backend", ctx["module_details"],
                      "module_details 应通过惰性解析填充")
        self.assertIn("mvn test", ctx["test_commands"],
                      "test_commands 应通过惰性解析填充")
        self.assertIn("dev-local", ctx["env_name"],
                      "env_name 应通过惰性解析填充")
        self.assertIn("backend-1", str(ctx["env_instances"]),
                      "env_instances 应通过惰性解析填充")
        self.assertEqual(ctx["review_level"], "",
                         "review_level 不在 tracks 段时默认为空")

        # 验证 YAML 块渲染
        self.assertIn("backend-1", ctx["env_instances_block"],
                      "env_instances_block 应包含实例名称")
        self.assertIn("yaml", ctx["env_instances_block"],
                      "env_instances_block 应包含 yaml 代码块标记")
        self.assertIn("localhost", ctx["hooks_block"],
                      "hooks_block 应包含环境信息")
        self.assertIn("role-backend-start.sh", ctx["hooks_block"],
                      "hooks_block 应包含 action 脚本路径")
        self.assertIn("hooks_yaml", ctx,
                      "hooks_yaml 应存在于上下文中")
        self.assertIn("start", ctx["hooks_yaml"],
                      "hooks_yaml 应包含 action 名称")

        # 验证 tasks_validation 来自 design.md 的 Verification Criteria
        self.assertIn("V-backend-1", ctx["tasks_validation"],
                      "tasks_validation 应包含 design.md 的 V-* 表")
        self.assertIn("GET /documents", ctx["tasks_validation"],
                      "tasks_validation 应包含设计验证项")
        self.assertNotIn("V-frontend-1", ctx["tasks_validation"],
                         "tasks_validation 不应包含其他 track 的验证项")

        # 清理缓存
        _PROJECT_CONFIG_CACHE.clear()
        _set_project_root("")


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
        self.assertIn("dispatch_seq", result)
        self.assertIn("report_seq", result)
        self.assertEqual(result["dispatch_seq"], "001")
        self.assertEqual(result["report_seq"], "002")

    def test_build_action_seq_prefix_in_filename(self):
        """dispatch 文件名带 seq 前缀。"""
        state = PipelineState(change="x", pipeline_order=("backend",))
        action = PipelineAction(kind="dispatch", track="backend", phase="test", cycle=1)
        tmp = tempfile.mkdtemp()
        result = build_action(state, action, tmp)
        fname = os.path.basename(result["dispatch_file"])
        self.assertTrue(fname.startswith("001-"), f"文件名应以 001- 开头: {fname}")

    def test_build_action_seq_increments(self):
        """连续两次 build_action seq 递增。"""
        state = PipelineState(change="x", pipeline_order=("backend",))
        tmp = tempfile.mkdtemp()
        a1 = PipelineAction(kind="dispatch", track="backend", phase="test", cycle=1)
        r1 = build_action(state, a1, tmp)
        a2 = PipelineAction(kind="dispatch", track="backend", phase="dev", cycle=1)
        r2 = build_action(state, a2, tmp)
        self.assertEqual(r1["dispatch_seq"], "001")
        self.assertEqual(r2["dispatch_seq"], "002")

    def test_build_final_gate(self):
        state = PipelineState(change="x")
        tmp = tempfile.mkdtemp()
        result = build_final_gate_action(state, tmp)
        self.assertEqual(result["action"], "dispatch_final_gate")
        self.assertEqual(result["item"], "final-gate")
        self.assertIn("dispatch_seq", result)
        self.assertIn("report_seq", result)


if __name__ == "__main__":
    unittest.main()