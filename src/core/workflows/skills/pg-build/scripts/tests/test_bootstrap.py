"""Bootstrap / Migrate / Git 操作测试。"""

from __future__ import annotations

import json
import os
import subprocess
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

    def test_plan_no_env_in_manifest_returns_skipped(self):
        """v2: execution-manifest.yaml 与 project.yaml 都不含 env → skipped。"""
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
        manifest_path = os.path.join(change_root, "execution-manifest.yaml")
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write("stages: []\n")
        plan = bootstrap._build_env_hook_plan("test-change", "prepare_env", explicit_stage_name="dev")
        self.assertTrue(plan.get("skipped"))


class TestAssertDefaultBranch(unittest.TestCase):
    """git.default_branch 守卫测试（修复 1a）。

    assert_default_branch 只检查本地分支，不执行 sys.exit。
    feat/pg/<change> 的放行由 caller 决定。
    """

    def setUp(self):
        """准备 tempfile + 初始化 git repo + 默认配置"""
        self.tmp = tempfile.mkdtemp()
        self.old_root = os.environ.get("PG_PROJECT_ROOT")
        os.environ["PG_PROJECT_ROOT"] = self.tmp
        bootstrap.PROJECT_ROOT = self.tmp
        bootstrap.CHANGES_DIR = os.path.join(self.tmp, ".pg", "changes")

        # git init
        subprocess.run(["git", "init", "-q"], cwd=self.tmp, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.tmp, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=self.tmp, check=True,
        )
        # 创建初始 commit (避免 detached HEAD)
        (Path(self.tmp) / "README.md").write_text("init")
        subprocess.run(["git", "add", "-A"], cwd=self.tmp, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init", "-q"],
            cwd=self.tmp, check=True,
        )

    def tearDown(self):
        if self.old_root:
            os.environ["PG_PROJECT_ROOT"] = self.old_root
        else:
            os.environ.pop("PG_PROJECT_ROOT", None)

    def _checkout(self, branch: str):
        """在测试 repo 内 checkout 指定分支（不存在则创建）"""
        r = subprocess.run(
            ["git", "checkout", "-q", branch],
            cwd=self.tmp, capture_output=True, text=True,
        )
        if r.returncode != 0:
            subprocess.run(
                ["git", "checkout", "-q", "-b", branch],
                cwd=self.tmp, check=True,
            )

    def test_matches_default_branch(self):
        """当前在 master, default_branch=master → ok=True"""
        self._checkout("master")
        config = {"git": {"default_branch": "master"}}
        result = bootstrap.assert_default_branch(self.tmp, config)
        self.assertTrue(result["ok"])
        self.assertEqual(result["current_branch"], "master")
        self.assertEqual(result["expected_branch"], "master")

    def test_mismatched_branch_returns_false(self):
        """当前在 vxlan, default_branch=master → ok=False"""
        self._checkout("vxlan")
        config = {"git": {"default_branch": "master"}}
        result = bootstrap.assert_default_branch(self.tmp, config)
        self.assertFalse(result["ok"])
        self.assertEqual(result["current_branch"], "vxlan")
        self.assertEqual(result["expected_branch"], "master")

    def test_uses_master_when_config_missing(self):
        """project.yaml 无 git 段 → expected 默认 master"""
        self._checkout("master")
        config = {}  # 无 git 段
        result = bootstrap.assert_default_branch(self.tmp, config)
        self.assertTrue(result["ok"])
        self.assertEqual(result["expected_branch"], "master")

    def test_uses_master_when_git_section_empty(self):
        """git: {} 无 default_branch → expected 默认 master"""
        self._checkout("master")
        config = {"git": {}}
        result = bootstrap.assert_default_branch(self.tmp, config)
        self.assertTrue(result["ok"])
        self.assertEqual(result["expected_branch"], "master")

    def test_non_master_default_branch(self):
        """default_branch=main, 当前在 main → ok=True"""
        self._checkout("main")
        config = {"git": {"default_branch": "main"}}
        result = bootstrap.assert_default_branch(self.tmp, config)
        self.assertTrue(result["ok"])
        self.assertEqual(result["current_branch"], "main")
        self.assertEqual(result["expected_branch"], "main")

    def test_detects_dirty_working_tree(self):
        """在 default_branch 上有未提交变更 → ok=False, dirty=True"""
        self._checkout("master")
        config = {"git": {"default_branch": "master"}}
        (Path(self.tmp) / "dirty.txt").write_text("uncommitted")
        result = bootstrap.assert_default_branch(self.tmp, config)
        self.assertFalse(result["ok"])
        self.assertTrue(result["dirty"])
        self.assertIsNotNone(result["error"])

    def test_does_not_exit_or_throw(self):
        """assert_default_branch 不得抛异常或 sys.exit（由 caller 决定协议）"""
        self._checkout("any-random-branch")
        config = {"git": {"default_branch": "master"}}
        # 不应抛任何异常
        result = bootstrap.assert_default_branch(self.tmp, config)
        self.assertIn("ok", result)
        self.assertIn("error", result)


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

    def _create_fake_log(self, phase_name: str = "prepare_env") -> str:
        """创建假 log 文件，供 cli_env_action_result success=true 验证通过。"""
        build_dir = os.path.join(self.change_root, "2-build")
        log_path = os.path.join(build_dir, f"{phase_name}-fake.log")
        with open(log_path, "w") as f:
            f.write("fake log\n")
        return log_path

    def test_cli_bootstrap_structure(self):
        """cli_bootstrap 返回正确结构 (无项目配置时 env_hook_plan 为 None)。"""
        result = bootstrap.cli_bootstrap("test-change")
        self.assertEqual(result["action"], "bootstrap_result")
        self.assertIn("ok", result)
        self.assertIn("init_commit", result)
        self.assertIn("env_hook_plan", result)
        self.assertIn("pipeline_config", result)
        self.assertIsNone(result["env_hook_plan"])

    def test_cli_auto_reset_no_state(self):
        """无 2-build/ 状态文件 → reset=False。"""
        build_dir = os.path.join(self.change_root, "2-build")
        os.makedirs(build_dir, exist_ok=True)
        result = bootstrap.cli_auto_reset("test-change")
        self.assertFalse(result["reset"])
        self.assertIn("reason", result)

    def test_cli_auto_reset_no_terminal_state(self):
        """events/snapshot 存在但非 terminal → reset=False。"""
        build_dir = os.path.join(self.change_root, "2-build")
        os.makedirs(build_dir, exist_ok=True)
        events_path = os.path.join(build_dir, "pipeline.events")
        snapshot_path = os.path.join(build_dir, "pipeline.snapshot.json")
        # events 末尾是 pipeline_started（运行中）
        with open(events_path, "w") as fh:
            fh.write('{"ts":"2026-07-16T10:00:00+08:00","type":"pipeline_started","data":{"change":"test-change"}}\n')
        # snapshot status=running（运行中）
        with open(snapshot_path, "w") as fh:
            json.dump({"status": "running", "change": "test-change"}, fh)
        result = bootstrap.cli_auto_reset("test-change")
        self.assertFalse(result["reset"])
        # 状态文件应原样保留
        self.assertTrue(os.path.isfile(events_path))
        self.assertTrue(os.path.isfile(snapshot_path))

    def test_cli_auto_reset_workflow_failed_in_events(self):
        """events 末尾是 workflow_failed → reset=True，删 events+snapshot。"""
        build_dir = os.path.join(self.change_root, "2-build")
        os.makedirs(build_dir, exist_ok=True)
        events_path = os.path.join(build_dir, "pipeline.events")
        snapshot_path = os.path.join(build_dir, "pipeline.snapshot.json")
        with open(events_path, "w") as fh:
            fh.write('{"ts":"2026-07-16T10:00:00+08:00","type":"pipeline_started","data":{}}\n')
            fh.write('{"ts":"2026-07-16T10:01:00+08:00","type":"workflow_failed","data":{"reason":"test"}}\n')
        with open(snapshot_path, "w") as fh:
            json.dump({"status": "failed", "change": "test-change"}, fh)

        # 还要存一个"工件"文件验证它不被删
        artifact_path = os.path.join(build_dir, "001-test-dispatch.md")
        with open(artifact_path, "w") as fh:
            fh.write("# dispatch\n")

        result = bootstrap.cli_auto_reset("test-change")
        self.assertTrue(result["reset"])
        self.assertEqual(result["reason"], "event_log_last_workflow_failed")
        self.assertIn("pipeline.events", result["removed"])
        self.assertIn("pipeline.snapshot.json", result["removed"])
        # state 文件被删
        self.assertFalse(os.path.isfile(events_path))
        self.assertFalse(os.path.isfile(snapshot_path))
        # 工件保留
        self.assertTrue(os.path.isfile(artifact_path))

    def test_cli_auto_reset_snapshot_status_failed(self):
        """snapshot.status=failed（events 末尾不是 workflow_failed）→ reset=True。"""
        build_dir = os.path.join(self.change_root, "2-build")
        os.makedirs(build_dir, exist_ok=True)
        events_path = os.path.join(build_dir, "pipeline.events")
        snapshot_path = os.path.join(build_dir, "pipeline.snapshot.json")
        # events 末尾是正常事件（orphan state）
        with open(events_path, "w") as fh:
            fh.write('{"ts":"2026-07-16T10:00:00+08:00","type":"pipeline_started","data":{}}\n')
        # snapshot 状态为 failed
        with open(snapshot_path, "w") as fh:
            json.dump({"status": "failed", "change": "test-change"}, fh)

        result = bootstrap.cli_auto_reset("test-change")
        self.assertTrue(result["reset"])
        self.assertEqual(result["reason"], "snapshot_status_failed")
        self.assertFalse(os.path.isfile(events_path))
        self.assertFalse(os.path.isfile(snapshot_path))

    def test_cli_auto_reset_completed_state_preserved(self):
        """pipeline.status=completed → reset=False（已完成的 pipeline 不应被 reset）。"""
        build_dir = os.path.join(self.change_root, "2-build")
        os.makedirs(build_dir, exist_ok=True)
        events_path = os.path.join(build_dir, "pipeline.events")
        snapshot_path = os.path.join(build_dir, "pipeline.snapshot.json")
        with open(events_path, "w") as fh:
            fh.write('{"ts":"2026-07-16T10:00:00+08:00","type":"pipeline_completed","data":{}}\n')
        with open(snapshot_path, "w") as fh:
            json.dump({"status": "completed", "change": "test-change"}, fh)

        result = bootstrap.cli_auto_reset("test-change")
        self.assertFalse(result["reset"])
        # 状态文件应原样保留（不能误删已完成的 pipeline）
        self.assertTrue(os.path.isfile(events_path))
        self.assertTrue(os.path.isfile(snapshot_path))

    def test_cli_bootstrap_calls_auto_reset(self):
        """cli_bootstrap 在 2-build/ 有 workflow_failed 时应触发 reset 并写入 result['auto_reset']。"""
        build_dir = os.path.join(self.change_root, "2-build")
        os.makedirs(build_dir, exist_ok=True)
        events_path = os.path.join(build_dir, "pipeline.events")
        snapshot_path = os.path.join(build_dir, "pipeline.snapshot.json")
        with open(events_path, "w") as fh:
            fh.write('{"ts":"2026-07-16T10:00:00+08:00","type":"workflow_failed","data":{"reason":"x"}}\n')
        with open(snapshot_path, "w") as fh:
            json.dump({"status": "failed"}, fh)

        # 重写 project.yaml 以让 bootstrap 走到 env_hook_plan 阶段
        project_yaml = os.path.join(self.tmp, ".pg", "project.yaml")
        os.makedirs(os.path.dirname(project_yaml), exist_ok=True)
        with open(project_yaml, "w", encoding="utf-8") as f:
            f.write("environments: {}\nstages: []\n")
        manifest_path = os.path.join(self.change_root, "execution-manifest.yaml")
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write("stages: []\n")

        result = bootstrap.cli_bootstrap("test-change")
        # bootstrap result 应包含 auto_reset 字段且 reset=True
        self.assertIn("auto_reset", result, "cli_bootstrap 应在 result 中暴露 auto_reset 结果")
        self.assertTrue(result["auto_reset"]["reset"])
        # state 文件应被删
        self.assertFalse(os.path.isfile(events_path))
        self.assertFalse(os.path.isfile(snapshot_path))

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
        """v2: execution-manifest.yaml 缺 stage → cli_env_action 返回 skipped=true。"""
        manifest_path = os.path.join(self.change_root, "execution-manifest.yaml")
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write("stages: []\n")
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
        log_path = self._create_fake_log("prepare_env")

        result = bootstrap.cli_env_action_result(
            "test-change", "prepare_env", "dev", "dev-local",
            success=True, log_path=log_path, exit_code=0,
        )
        self.assertTrue(result["ok"])
        self.assertIn("dev", result["stage_prepared"])
        self.assertEqual(result["current_stage"], "dev")

    def test_cli_env_action_result_success_without_execution_is_rejected(self):
        """v2.7: success=true 但 hook 未实际执行（无 log）时应拒绝。"""
        from pipeline.snapshot import save_snapshot, load_snapshot
        state = PipelineState(
            change="test-change",
            stage_order=("dev",),
            stage_env_map={"dev": "dev-local"},
            current_stage="",
            stage_prepared=set(),
        )
        save_snapshot(self.change_root, state)

        result = bootstrap.cli_env_action_result(
            "test-change", "prepare_env", "dev", "dev-local",
            success=True, log_path="/tmp/definitely-not-created.log", exit_code=0,
        )
        self.assertFalse(result["ok"])
        self.assertIn("请先 bash 执行 env_hook_plan.command", result["error"])
        state_after = load_snapshot(self.change_root)
        self.assertNotIn("dev", state_after.stage_prepared)

    def test_cli_env_action_result_clean_env(self):
        """clean_env 成功: stage_prepared 移除 stage, current_stage 推进到下一 stage (v3.14 修复 1a)。"""
        from pipeline.snapshot import save_snapshot
        state = PipelineState(
            change="test-change",
            stage_order=("dev", "integration"),
            stage_env_map={"dev": "dev-local"},
            current_stage="dev",
            stage_prepared={"dev"},
        )
        save_snapshot(self.change_root, state)
        log_path = self._create_fake_log("clean_env")

        result = bootstrap.cli_env_action_result(
            "test-change", "clean_env", "dev", "dev-local",
            success=True, log_path=log_path, exit_code=0,
        )
        self.assertTrue(result["ok"])
        self.assertNotIn("dev", result["stage_prepared"])
        # v3.14 (修复 1a): clean_env 推进 current_stage 到下一 stage
        self.assertEqual(result["current_stage"], "integration")

    def test_cli_env_action_result_clean_env_last_stage_no_advance(self):
        """v3.14 (修复 1a): 最后一个 stage clean 后 current_stage 不推进。"""
        from pipeline.snapshot import save_snapshot
        state = PipelineState(
            change="test-change",
            stage_order=("dev", "integration"),
            stage_env_map={"integration": "dev-3tier"},
            current_stage="integration",
            stage_prepared={"integration"},
        )
        save_snapshot(self.change_root, state)
        log_path = self._create_fake_log("clean_env")

        result = bootstrap.cli_env_action_result(
            "test-change", "clean_env", "integration", "dev-3tier",
            success=True, log_path=log_path, exit_code=0,
        )
        self.assertTrue(result["ok"])
        self.assertNotIn("integration", result["stage_prepared"])
        self.assertEqual(result["current_stage"], "integration")

    def test_cli_env_action_result_clean_env_stage_mismatch_no_advance(self):
        """v3.14 (修复 1a): clean 的 stage 与 current_stage 不一致时不推进（防御性）。"""
        from pipeline.snapshot import save_snapshot
        state = PipelineState(
            change="test-change",
            stage_order=("env", "dev", "integration"),
            stage_env_map={"dev": "dev-local"},
            current_stage="dev",
            stage_prepared={"dev"},
        )
        save_snapshot(self.change_root, state)
        log_path = self._create_fake_log("clean_env")

        result = bootstrap.cli_env_action_result(
            "test-change", "clean_env", "env", "dev-local",
            success=True, log_path=log_path, exit_code=0,
        )
        self.assertTrue(result["ok"])
        # env != current_stage(dev) → 不推进
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
            success=False, log_path="/tmp/fake.log", exit_code=1,
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
        log1 = self._create_fake_log("prepare_env")
        r1 = bootstrap.cli_env_action_result(
            "test-change", "prepare_env", "dev", "dev-local",
            success=True, log_path=log1, exit_code=0,
        )
        self.assertTrue(r1["ok"])
        self.assertEqual(r1["stage_prepared"], ["dev"])
        self.assertEqual(r1["current_stage"], "dev")

        # 2) clean_env dev (dev 的工作完成后)
        log2 = self._create_fake_log("clean_env")
        r2 = bootstrap.cli_env_action_result(
            "test-change", "clean_env", "dev", "dev-local",
            success=True, log_path=log2, exit_code=0,
        )
        self.assertTrue(r2["ok"])
        self.assertEqual(r2["stage_prepared"], [])
        # v3.14 (修复 1a): clean_env 推进 current_stage 到下一 stage
        self.assertEqual(r2["current_stage"], "integration")

        # 3) prepare_env integration
        log3 = self._create_fake_log("prepare_env")
        r3 = bootstrap.cli_env_action_result(
            "test-change", "prepare_env", "integration", "dev-3tier",
            success=True, log_path=log3, exit_code=0,
        )
        self.assertTrue(r3["ok"])
        self.assertEqual(sorted(r3["stage_prepared"]), ["integration"])
        self.assertEqual(r3["current_stage"], "integration")

        final = load_snapshot(self.change_root)
        self.assertIsNotNone(final)
        self.assertEqual(final.stage_prepared, {"integration"})
        self.assertEqual(final.current_stage, "integration")

    def test_cli_env_action_result_param_renamed(self):
        """v2.x: 参数名 ok → success，向后不兼容（破坏性变更）"""
        # 旧调用 ok=True 必须报错（TypeError: unexpected keyword）
        with self.assertRaises(TypeError):
            bootstrap.cli_env_action_result(
                "test-change", "prepare_env", "dev", "dev-local",
                ok=True, log_path="/tmp/fake.log", exit_code=0,
            )

    def test_runner_env_action_result_rejects_ok_string(self):
        """runner CLI: 不再兼容 'ok' 字符串（破坏性变更）"""
        import subprocess
        import sys
        runner_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "pg-pipeline-runner.py"))
        result = subprocess.run(
            [sys.executable, runner_path,
             "env-action-result", "test-change",
             "--phase", "prepare_env", "--stage", "dev", "--env", "dev-local",
             "--success", "ok"],
            capture_output=True, text=True,
        )
        # 期望 argparse 拒绝
        self.assertIn("无效 success", result.stderr)
        self.assertNotEqual(result.returncode, 0)

    def test_runner_env_action_result_rejects_failed_string(self):
        """runner CLI: 不再兼容 'failed' 字符串（破坏性变更）"""
        import subprocess
        import sys
        runner_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "pg-pipeline-runner.py"))
        result = subprocess.run(
            [sys.executable, runner_path,
             "env-action-result", "test-change",
             "--phase", "prepare_env", "--stage", "dev", "--env", "dev-local",
             "--success", "failed"],
            capture_output=True, text=True,
        )
        # 期望 argparse 拒绝
        self.assertIn("无效 success", result.stderr)
        self.assertNotEqual(result.returncode, 0)

    def test_cli_bootstrap_detect_config_no_manifest(self):
        """无 manifest 时 pipeline_config 为默认值。"""
        result = bootstrap.cli_bootstrap("test-change")
        pc = result.get("pipeline_config", {})
        self.assertIn("pipeline_order", pc)
        self.assertIn("track_configs", pc)
        self.assertIn("stage_order", pc)
        self.assertIn("stage_env_map", pc)
        self.assertGreater(len(pc["stage_order"]), 0)

    def test_cli_bootstrap_restart_uses_scenario_track_stage(self):
        """v3.x fix: scenario track 首次进入需 restart 时, bootstrap 应从
        first_pending track id 提取正确 stage（如 'int'），而非 manifest
        第一个 stage（'dev'）。这避免了 restart plan 的 PG_STAGE=dev，
        导致 env-action-result --stage dev 找不到 int.scr track、
        scenario_last_restart_attempt 永远不更新、bootstrap 持续返回
        restart plan 的死循环。
        """
        from pipeline.snapshot import save_snapshot
        from pipeline.state import PipelineState, TrackState, PhaseState

        # dev.backend / dev.frontend 已 completed（只把 int.scr 留在 pending）
        # 这样 first_pending 直接落到 int.scr, 跳过 standard track 的检查
        completed_standard = TrackState(
            track_id="dev.backend", bare="backend", status="completed",
        )
        completed_frontend = TrackState(
            track_id="dev.frontend", bare="frontend", status="completed",
        )
        scenario_track = TrackState(
            track_id="int.scr",
            bare="scr",
            status="running",
            phases={
                "scenario-execute": PhaseState(
                    status="running",
                    attempt=0,
                ),
            },
            scenario_last_restart_attempt=-1,
        )
        state = PipelineState(
            change="test-change",
            pipeline_order=("dev.backend", "dev.frontend", "int.scr", "final-gate"),
            stage_order=("dev", "int"),
            stage_env_map={"dev": "dev-local", "int": "dev-local"},
            track_types={"dev.backend": "standard", "dev.frontend": "standard",
                         "int.scr": "scenario", "final-gate": "final-gate"},
            tracks={
                "dev.backend": completed_standard,
                "dev.frontend": completed_frontend,
                "int.scr": scenario_track,
            },
            current_stage="int",
            stage_prepared={"dev", "int"},
            status="running",
        )
        save_snapshot(self.change_root, state)

        # 写一个 manifest 使第一个 stage 是 'dev'（模拟 bug 场景）
        manifest_path = os.path.join(self.change_root, "execution-manifest.yaml")
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write(
                "stages:\n"
                "  - name: dev\n"
                "    environment: dev-local\n"
                "    tracks: [dev.backend, dev.frontend]\n"
                "  - name: int\n"
                "    environment: dev-local\n"
                "    tracks: [int.scr]\n"
            )

        # 写一个 project.yaml 让 restart plan 能构造出 command
        # 注意: restart phase 需要 env_cfg["roles"] 非空才返回非 skipped plan
        project_yaml = os.path.join(self.tmp, ".pg", "project.yaml")
        with open(project_yaml, "w") as f:
            f.write(
                "environments:\n"
                "  dev-local:\n"
                "    roles:\n"
                "      - backend\n"
                "      - frontend\n"
            )

        # _build_env_hook_plan 检查 pg-invoke-hook.py 是否存在;
        # stub 一个空文件让 plan build 继续。
        invoke_hook = os.path.join(
            self.tmp, ".pg", "skills", "src", "runtime", "bin",
            "pg-invoke-hook.py",
        )
        os.makedirs(os.path.dirname(invoke_hook), exist_ok=True)
        with open(invoke_hook, "w") as f:
            f.write("# stub\n")

        result = bootstrap.cli_bootstrap("test-change")
        self.assertTrue(result["ok"], f"bootstrap failed: {result.get('error')}")
        plan = result["env_hook_plan"]
        # 关键断言: restart plan 应使用 first_pending 的 stage (int)，
        # 不是 manifest 第一个 stage (dev)
        self.assertIsNotNone(plan, "expected env_hook_plan, got None")
        # stage_name == int 表明从 first_pending 正确提取了 stage
        self.assertEqual(plan["stage_name"], "int",
                         f"restart plan stage_name 错误: {plan['stage_name']}，"
                         f"应为 'int'（first_pending=int.scr 的 stage）")
        # env_name 应等于 manifest 中 int stage 的 environment
        self.assertEqual(plan["env_name"], "dev-local")
        # 命令中 PG_STAGE 也应等于 int
        self.assertIn("PG_STAGE=int", plan["command"],
                      f"command 中 PG_STAGE 不是 int: {plan['command'][:200]}")
        # 反向断言: 若 fix 没生效, plan 应使用 dev（manifest 第一个 stage）
        self.assertNotIn("PG_STAGE=dev ", plan["command"],
                         f"command 中 PG_STAGE 错误取 dev: {plan['command'][:200]}")


class TestDetectFailedStateScenarioExhaustion(unittest.TestCase):
    """v1.1.0 (P0-2): _detect_failed_state 暴露 scenario fix_cycle 耗尽细节。"""

    def _setup_change_dir(self, snapshot: dict) -> str:
        change = f"test-detect-{os.urandom(4).hex()}"
        build_dir = os.path.join(bootstrap.CHANGES_DIR, change, bootstrap.APPLY_DIR)
        os.makedirs(build_dir, exist_ok=True)
        # event log 最后一行是 workflow_failed
        with open(os.path.join(build_dir, "pipeline.events"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "workflow_failed", "detail": {
                "track": "int.scr", "phase": "scenario-execute",
            }}) + "\n")
        with open(os.path.join(build_dir, "pipeline.snapshot.json"), "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh)
        return change

    def test_scenario_exhaustion_surfaced(self):
        snapshot = {
            "status": "failed",
            "failed_reason": "int.scr:scenario-execute fix cycles exhausted (8/8)",
            "current_track": "int.scr",
            "current_phase": "scenario-execute",
            "tracks": {
                "int.scr": {
                    "max_fix_retries": 8,
                    "scenario_max_fix_cycles": 8,
                    "phases": {
                        "scenario-execute": {
                            "fix_cycles": [{"cycle": i, "status": "completed"} for i in range(1, 9)],
                        },
                    },
                },
            },
        }
        change = self._setup_change_dir(snapshot)
        result = bootstrap._detect_failed_state(change)
        self.assertTrue(result["detected"])
        self.assertEqual(result.get("failure_mode"), "scenario_fix_cycles_exhausted")
        self.assertEqual(result.get("fix_cycles_count"), 8)
        self.assertEqual(result.get("max_allowed"), 8)

    def test_scenario_fallback_to_max_fix_retries(self):
        """scenario_max_fix_cycles 未设置时 max_allowed 回退到 max_fix_retries。"""
        snapshot = {
            "status": "failed",
            "failed_reason": "int.scr:scenario-execute fix cycles exhausted (5/5)",
            "current_track": "int.scr",
            "current_phase": "scenario-execute",
            "tracks": {
                "int.scr": {
                    "max_fix_retries": 5,
                    "phases": {
                        "scenario-execute": {
                            "fix_cycles": [{"cycle": i, "status": "completed"} for i in range(1, 6)],
                        },
                    },
                },
            },
        }
        change = self._setup_change_dir(snapshot)
        result = bootstrap._detect_failed_state(change)
        self.assertEqual(result.get("failure_mode"), "scenario_fix_cycles_exhausted")
        self.assertEqual(result.get("max_allowed"), 5)

    def test_non_scenario_failure_no_mode(self):
        """非 scenario-execute 的失败不带 failure_mode。"""
        snapshot = {
            "status": "failed",
            "failed_reason": "dev.backend:dev failed",
            "current_track": "dev.backend",
            "current_phase": "dev",
            "tracks": {},
        }
        change = self._setup_change_dir(snapshot)
        result = bootstrap._detect_failed_state(change)
        self.assertTrue(result["detected"])
        self.assertNotIn("failure_mode", result)


if __name__ == "__main__":
    unittest.main()
