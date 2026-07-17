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

        result = bootstrap.cli_env_action_result(
            "test-change", "prepare_env", "dev", "dev-local",
            success=True, log_path="/tmp/fake.log", exit_code=0,
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
            success=True, log_path="/tmp/fake.log", exit_code=0,
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
        r1 = bootstrap.cli_env_action_result(
            "test-change", "prepare_env", "dev", "dev-local",
            success=True, log_path="/tmp/1.log", exit_code=0,
        )
        self.assertTrue(r1["ok"])
        self.assertEqual(r1["stage_prepared"], ["dev"])
        self.assertEqual(r1["current_stage"], "dev")

        # 2) clean_env dev (dev 的工作完成后)
        r2 = bootstrap.cli_env_action_result(
            "test-change", "clean_env", "dev", "dev-local",
            success=True, log_path="/tmp/2.log", exit_code=0,
        )
        self.assertTrue(r2["ok"])
        self.assertEqual(r2["stage_prepared"], [])
        self.assertEqual(r2["current_stage"], "dev")  # current_stage 不变

        # 3) prepare_env integration
        r3 = bootstrap.cli_env_action_result(
            "test-change", "prepare_env", "integration", "dev-3tier",
            success=True, log_path="/tmp/3.log", exit_code=0,
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


if __name__ == "__main__":
    unittest.main()
