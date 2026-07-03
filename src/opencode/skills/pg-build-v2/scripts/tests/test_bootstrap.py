"""Bootstrap / Migrate / Git 操作测试。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.event_log import EventLog
from pipeline.state import PipelineState
import bootstrap


class TestMigrateLegacyFiles(unittest.TestCase):
    """_migrate_files_impl 测试。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_migrate_creates_2build(self):
        change_root = os.path.join(self.tmp, "test-change")
        os.makedirs(change_root, exist_ok=True)

        moved = bootstrap._migrate_files_impl(change_root)
        self.assertEqual(moved, [])

        apply_dir = os.path.join(change_root, "2-build")
        self.assertTrue(os.path.isdir(apply_dir))

    def test_migrate_moves_legacy_state(self):
        change_root = os.path.join(self.tmp, "test-change")
        os.makedirs(change_root, exist_ok=True)
        legacy = os.path.join(change_root, ".pipeline-state.json")
        with open(legacy, "w") as f:
            json.dump({"version": 1}, f)

        moved = bootstrap._migrate_files_impl(change_root)
        self.assertTrue(any("pipeline-state" in m for m in moved))
        self.assertFalse(os.path.isfile(legacy))
        target = os.path.join(change_root, "2-build", ".pipeline-state.json")
        self.assertTrue(os.path.isfile(target))

    def test_migrate_removes_orphan_files(self):
        change_root = os.path.join(self.tmp, "test-change")
        os.makedirs(change_root, exist_ok=True)
        apply_dir = os.path.join(change_root, "2-build")
        os.makedirs(apply_dir, exist_ok=True)

        legacy = os.path.join(change_root, ".pipeline-state.json")
        with open(legacy, "w") as f:
            f.write("{}")
        target = os.path.join(apply_dir, ".pipeline-state.json")
        with open(target, "w") as f:
            f.write('{"v":2}')

        moved = bootstrap._migrate_files_impl(change_root)
        self.assertFalse(os.path.isfile(legacy))
        self.assertTrue(os.path.isfile(target))


class TestBootstrapEnvHook(unittest.TestCase):
    """execute_env_hook_inline 测试。"""

    def test_no_project_yaml(self):
        tmp = tempfile.mkdtemp()
        old = os.environ.get("PG_PROJECT_ROOT")
        os.environ["PG_PROJECT_ROOT"] = tmp
        try:
            result = bootstrap.execute_env_hook_inline("test-change", "prepare_env")
            self.assertTrue(result.get("skipped"))
        finally:
            if old:
                os.environ["PG_PROJECT_ROOT"] = old
            else:
                os.environ.pop("PG_PROJECT_ROOT", None)


class TestBuildEnvHookPlan(unittest.TestCase):
    """_build_env_hook_plan 单元测试 (v2.1.1)。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old_root = os.environ.get("PG_PROJECT_ROOT")
        os.environ["PG_PROJECT_ROOT"] = self.tmp
        os.makedirs(os.path.join(self.tmp, ".pg"), exist_ok=True)
        with open(os.path.join(self.tmp, ".pg", "project.yaml"), "w") as f:
            f.write("environments: {}\n")
        bootstrap.PROJECT_ROOT = self.tmp
        bootstrap.CHANGES_DIR = os.path.join(self.tmp, ".pg", "changes")

    def tearDown(self):
        if self.old_root:
            os.environ["PG_PROJECT_ROOT"] = self.old_root
        else:
            os.environ.pop("PG_PROJECT_ROOT", None)

    def test_plan_no_project_yaml_returns_skipped(self):
        plan = bootstrap._build_env_hook_plan("test-change", "prepare_env")
        self.assertTrue(plan.get("ok"))
        self.assertTrue(plan.get("skipped"))

    def test_plan_invalid_phase(self):
        plan = bootstrap._build_env_hook_plan("test-change", "bogus")
        self.assertFalse(plan.get("ok"))
        self.assertIn("invalid phase", plan.get("error", ""))

    def test_plan_with_project_yaml(self):
        project_yaml = os.path.join(self.tmp, ".pg", "project.yaml")
        os.makedirs(os.path.dirname(project_yaml), exist_ok=True)
        with open(project_yaml, "w", encoding="utf-8") as f:
            f.write("""
environments:
  test-env:
    prepare_env:
      script: .pg/hooks/fake.sh
      timeout_seconds: 333
""")
        change_root = os.path.join(self.tmp, ".pg", "changes", "test-change")
        os.makedirs(change_root, exist_ok=True)
        with open(os.path.join(change_root, "execution-manifest.yaml"), "w") as f:
            f.write("""
stages:
  - name: dev
    environment: test-env
    tracks:
      - id: backend
""")

        plan = bootstrap._build_env_hook_plan("test-change", "prepare_env", explicit_stage_name="dev")
        self.assertTrue(plan.get("ok"))
        self.assertFalse(plan.get("skipped"))
        self.assertEqual(plan["env_name"], "test-env")
        self.assertEqual(plan["stage_name"], "dev")
        self.assertEqual(plan["timeout_seconds"], 333)
        self.assertIn("command", plan)
        self.assertIn("env", plan)
        self.assertEqual(plan["env"].get("PG_ENV"), "test-env")
        self.assertEqual(plan["env"].get("PG_STAGE"), "dev")
        self.assertEqual(plan["env"].get("PG_HOOK_TYPE"), "prepare_env")

    def test_plan_with_explicit_timeout(self):
        project_yaml = os.path.join(self.tmp, ".pg", "project.yaml")
        os.makedirs(os.path.dirname(project_yaml), exist_ok=True)
        with open(project_yaml, "w", encoding="utf-8") as f:
            f.write("""
environments:
  test-env:
    prepare_env:
      script: /tmp/fake.sh
      timeout_seconds: 100
""")
        plan = bootstrap._build_env_hook_plan(
            "test-change", "prepare_env",
            explicit_env_name="test-env", explicit_stage_name="dev",
            explicit_timeout=999,
        )
        self.assertEqual(plan["timeout_seconds"], 999)

    def test_plan_respects_explicit_stage_name(self):
        """关键回归测试: 多 stage 时, plan 不应取第一个有 env 的 stage。"""
        project_yaml = os.path.join(self.tmp, ".pg", "project.yaml")
        os.makedirs(os.path.dirname(project_yaml), exist_ok=True)
        with open(project_yaml, "w", encoding="utf-8") as f:
            f.write("""
environments:
  env-a:
    prepare_env:
      script: /tmp/a.sh
  env-b:
    prepare_env:
      script: /tmp/b.sh
""")
        change_root = os.path.join(self.tmp, ".pg", "changes", "test-change")
        os.makedirs(change_root, exist_ok=True)
        with open(os.path.join(change_root, "execution-manifest.yaml"), "w") as f:
            f.write("""
stages:
  - name: dev
    environment: env-a
    tracks:
      - id: backend
  - name: integration
    environment: env-b
    tracks:
      - id: backend
""")

        plan_dev = bootstrap._build_env_hook_plan("test-change", "prepare_env", explicit_stage_name="dev")
        self.assertEqual(plan_dev["env_name"], "env-a")

        plan_int = bootstrap._build_env_hook_plan("test-change", "prepare_env", explicit_stage_name="integration")
        self.assertEqual(plan_int["env_name"], "env-b")

    def test_plan_environment_yaml_skip(self):
        project_yaml = os.path.join(self.tmp, ".pg", "project.yaml")
        os.makedirs(os.path.dirname(project_yaml), exist_ok=True)
        with open(project_yaml, "w", encoding="utf-8") as f:
            f.write("""
environments:
  test-env:
    prepare_env:
      script: /tmp/fake.sh
""")
        change_root = os.path.join(self.tmp, ".pg", "changes", "test-change")
        os.makedirs(change_root, exist_ok=True)
        with open(os.path.join(change_root, "environment.yaml"), "w") as f:
            f.write("dev: skip\n")
        plan = bootstrap._build_env_hook_plan("test-change", "prepare_env", explicit_stage_name="dev")
        self.assertTrue(plan.get("skipped"))


class TestCliBootstrap(unittest.TestCase):
    """cli_bootstrap / cli_env_action / cli_env_action_result 测试 (v2.1.1)。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old_root = os.environ.get("PG_PROJECT_ROOT")
        os.environ["PG_PROJECT_ROOT"] = self.tmp

        os.makedirs(os.path.join(self.tmp, ".pg"), exist_ok=True)
        with open(os.path.join(self.tmp, ".pg", "project.yaml"), "w") as f:
            f.write("environments: {}\n")

        bootstrap.PROJECT_ROOT = self.tmp
        bootstrap.CHANGES_DIR = os.path.join(self.tmp, ".pg", "changes")
        self.change_root = bootstrap.CHANGES_DIR + "/test-change"
        os.makedirs(os.path.join(self.change_root, "2-build"), exist_ok=True)

    def tearDown(self):
        if self.old_root:
            os.environ["PG_PROJECT_ROOT"] = self.old_root
        else:
            os.environ.pop("PG_PROJECT_ROOT", None)

    def test_cli_bootstrap_structure(self):
        """cli_bootstrap 返回正确结构 (无项目配置时 env_hook_plan 为 None)。"""
        result = bootstrap.cli_bootstrap("test-change")
        self.assertEqual(result["action"], "bootstrap_result")
        self.assertIn("ok", result)
        self.assertIn("init_commit", result)
        self.assertIn("env_hook_plan", result)
        self.assertIn("pipeline_config", result)
        self.assertIsNone(result["env_hook_plan"])

    def test_cli_bootstrap_does_not_execute_env_hook(self):
        """v2.1.1 关键回归测试: cli_bootstrap 不得同步执行 env hook。"""
        project_yaml = os.path.join(self.tmp, ".pg", "project.yaml")
        os.makedirs(os.path.dirname(project_yaml), exist_ok=True)
        with open(project_yaml, "w", encoding="utf-8") as f:
            f.write("""
environments:
  test-env:
    prepare_env:
      script: /nonexistent/should/not/be/executed.sh
      timeout_seconds: 600
stages:
  - name: dev
    environment:
      name: test-env
      required: true
""")
        manifest_path = os.path.join(self.change_root, "execution-manifest.yaml")
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write("""
stages:
  - name: dev
    environment: test-env
    tracks:
      - id: backend
""")
        result = bootstrap.cli_bootstrap("test-change")
        self.assertTrue(result["ok"], f"cli_bootstrap 失败: {result.get('error')}")
        self.assertIsNotNone(result["env_hook_plan"])
        plan = result["env_hook_plan"]
        self.assertEqual(plan["env_name"], "test-env")
        self.assertEqual(plan["stage_name"], "dev")
        self.assertEqual(plan["timeout_seconds"], 600)
        self.assertIn("command", plan)
        self.assertIn("log_path", plan)
        self.assertNotIn("exit_code", plan)
        self.assertNotIn("success", plan)

    def test_cli_env_action_structure(self):
        """cli_env_action 返回 plan-only 结构。"""
        result = bootstrap.cli_env_action("test-change", "prepare_env", "dev", "dev-local")
        self.assertEqual(result["action"], "env_action_plan")
        self.assertIn("ok", result)
        self.assertEqual(result["phase"], "prepare_env")
        self.assertEqual(result["stage"], "dev")
        self.assertEqual(result["env_name"], "dev-local")
        self.assertIn("started_event_ts", result)

    def test_cli_env_action_clean_env(self):
        """cli_env_action 支持 clean_env phase。"""
        result = bootstrap.cli_env_action("test-change", "clean_env", "integration", "dev-3tier")
        self.assertEqual(result["action"], "env_action_plan")
        self.assertEqual(result["phase"], "clean_env")

    def test_cli_env_action_with_plan(self):
        """当 env hook 存在时, cli_env_action 返回完整 plan。"""
        os.makedirs(os.path.join(self.tmp, ".pg", "hooks"), exist_ok=True)
        with open(os.path.join(self.tmp, ".pg", "hooks", "fake-prepare.sh"), "w") as f:
            f.write("#!/bin/bash\necho ok\n")
        project_yaml = os.path.join(self.tmp, ".pg", "project.yaml")
        os.makedirs(os.path.dirname(project_yaml), exist_ok=True)
        with open(project_yaml, "w", encoding="utf-8") as f:
            f.write("""
environments:
  test-env:
    prepare_env:
      script: .pg/hooks/fake-prepare.sh
      timeout_seconds: 123
""")
        result = bootstrap.cli_env_action("test-change", "prepare_env", "dev", "test-env")
        self.assertTrue(result["ok"])
        self.assertFalse(result.get("skipped"))
        plan = result["plan"]
        self.assertIsNotNone(plan)
        self.assertEqual(plan["env_name"], "test-env")
        self.assertEqual(plan["timeout_seconds"], 123)
        self.assertIn("command", plan)
        self.assertIn("log_path", plan)
        self.assertNotIn("env", plan)
        # v2.1.1 fix: env vars inlined into command via env prefix
        self.assertIn("PG_PROJECT_ROOT=", plan["command"])
        self.assertIn("PG_ENV=test-env", plan["command"])
        self.assertIn("PG_HOOK_TYPE=prepare_env", plan["command"])
        self.assertTrue(plan["command"].startswith("env "))

    def test_cli_env_action_command_executable(self):
        """内联 env 前缀的 command 可被 subprocess 执行且正确传递变量。"""
        import subprocess
        hook_path = os.path.join(self.tmp, ".pg", "hooks", "fake-echo-env.sh")
        os.makedirs(os.path.dirname(hook_path), exist_ok=True)
        with open(hook_path, "w") as f:
            f.write("#!/bin/bash\necho \"PROJ=$PG_PROJECT_ROOT\"\necho \"ENV=$PG_ENV\"\n")
        os.chmod(hook_path, 0o755)

        project_yaml = os.path.join(self.tmp, ".pg", "project.yaml")
        with open(project_yaml, "w", encoding="utf-8") as f:
            f.write("environments:\n  test-env:\n    prepare_env:\n"
                    "      script: .pg/hooks/fake-echo-env.sh\n"
                    "      timeout_seconds: 30\n")
        change_root = os.path.join(self.tmp, ".pg", "changes", "test-change")
        os.makedirs(change_root, exist_ok=True)

        result = bootstrap.cli_env_action("test-change", "prepare_env", "dev", "test-env")
        self.assertTrue(result["ok"])
        plan = result["plan"]
        self.assertIsNotNone(plan)

        proc = subprocess.run(
            ["bash", "-c", plan["command"]],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr}")
        self.assertIn("PROJ=", proc.stdout)
        self.assertIn("ENV=test-env", proc.stdout)

    def test_cli_env_action_skipped(self):
        """environment.yaml 标 skip → cli_env_action 返回 skipped=true。"""
        env_yaml = os.path.join(self.change_root, "environment.yaml")
        with open(env_yaml, "w", encoding="utf-8") as f:
            f.write("dev: skip\n")
        result = bootstrap.cli_env_action("test-change", "prepare_env", "dev", "test-env")
        self.assertTrue(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertIsNone(result["plan"])

    def test_cli_env_action_result_ok(self):
        """cli_env_action_result 成功: 写 event + 更新 stage_prepared/current_stage。"""
        from pipeline.snapshot import save_snapshot
        state = PipelineState(
            change="test-change",
            stage_order=("dev", "integration"),
            stage_env_map={"dev": "dev-local", "integration": "dev-3tier"},
            stage_env_timeout={"dev-local": 600, "dev-3tier": 600},
            current_stage="",
            stage_prepared=set(),
            status="running",
        )
        save_snapshot(self.change_root, state)

        result = bootstrap.cli_env_action_result(
            "test-change", "prepare_env", "dev", "dev-local",
            ok=True, log_path="/tmp/fake.log", exit_code=0,
        )
        self.assertTrue(result["ok"])
        self.assertIn("dev", result["stage_prepared"])
        self.assertEqual(result["current_stage"], "dev")

    def test_cli_env_action_result_clean_env(self):
        """clean_env 成功: stage_prepared 移除 stage, current_stage 不变。"""
        from pipeline.snapshot import save_snapshot
        state = PipelineState(
            change="test-change",
            stage_order=("dev", "integration"),
            stage_env_map={"dev": "dev-local"},
            current_stage="dev",
            stage_prepared={"dev"},
        )
        save_snapshot(self.change_root, state)

        result = bootstrap.cli_env_action_result(
            "test-change", "clean_env", "dev", "dev-local",
            ok=True, log_path="/tmp/fake.log", exit_code=0,
        )
        self.assertTrue(result["ok"])
        self.assertNotIn("dev", result["stage_prepared"])
        # current_stage 不变 (由下一 prepare_env 成功才会更新)
        self.assertEqual(result["current_stage"], "dev")

    def test_cli_env_action_result_failed_does_not_update_state(self):
        """env hook 失败: 不更新 state。"""
        from pipeline.snapshot import save_snapshot, load_snapshot
        state = PipelineState(
            change="test-change",
            stage_order=("dev",),
            stage_env_map={"dev": "dev-local"},
            current_stage="dev",
            stage_prepared={"dev"},
        )
        save_snapshot(self.change_root, state)

        result = bootstrap.cli_env_action_result(
            "test-change", "prepare_env", "integration", "dev-3tier",
            ok=False, log_path="/tmp/fake.log", exit_code=1,
            error="synthetic failure",
        )
        self.assertFalse(result["ok"])
        self.assertIn("synthetic failure", result["error"])
        state_after = load_snapshot(self.change_root)
        self.assertIsNotNone(state_after)
        self.assertIn("dev", state_after.stage_prepared)
        self.assertEqual(state_after.current_stage, "dev")
        self.assertNotIn("integration", state_after.stage_prepared)

    def test_cli_env_action_result_multistage_sequence(self):
        """多 stage 完整流程: dev → integration, 验证 stage_prepared 状态机推进。"""
        from pipeline.snapshot import save_snapshot, load_snapshot
        state = PipelineState(
            change="test-change",
            stage_order=("dev", "integration"),
            stage_env_map={"dev": "dev-local", "integration": "dev-3tier"},
            current_stage="",
            stage_prepared=set(),
        )
        save_snapshot(self.change_root, state)

        # 1) prepare_env dev
        r1 = bootstrap.cli_env_action_result(
            "test-change", "prepare_env", "dev", "dev-local",
            ok=True, log_path="/tmp/1.log", exit_code=0,
        )
        self.assertTrue(r1["ok"])
        self.assertEqual(r1["stage_prepared"], ["dev"])
        self.assertEqual(r1["current_stage"], "dev")

        # 2) clean_env dev (dev 的工作完成后)
        r2 = bootstrap.cli_env_action_result(
            "test-change", "clean_env", "dev", "dev-local",
            ok=True, log_path="/tmp/2.log", exit_code=0,
        )
        self.assertTrue(r2["ok"])
        self.assertEqual(r2["stage_prepared"], [])
        self.assertEqual(r2["current_stage"], "dev")  # current_stage 不变

        # 3) prepare_env integration
        r3 = bootstrap.cli_env_action_result(
            "test-change", "prepare_env", "integration", "dev-3tier",
            ok=True, log_path="/tmp/3.log", exit_code=0,
        )
        self.assertTrue(r3["ok"])
        self.assertEqual(sorted(r3["stage_prepared"]), ["integration"])
        self.assertEqual(r3["current_stage"], "integration")

        final = load_snapshot(self.change_root)
        self.assertIsNotNone(final)
        self.assertEqual(final.stage_prepared, {"integration"})
        self.assertEqual(final.current_stage, "integration")

    def test_cli_bootstrap_detect_config_no_manifest(self):
        """无 manifest 时 pipeline_config 为默认值。"""
        result = bootstrap.cli_bootstrap("test-change")
        pc = result.get("pipeline_config", {})
        self.assertIn("pipeline_order", pc)
        self.assertIn("track_configs", pc)
        self.assertIn("stage_order", pc)
        self.assertIn("stage_env_map", pc)
        self.assertGreater(len(pc["stage_order"]), 0)


if __name__ == "__main__":
    unittest.main()
