"""Orchestrator._first_next() 测试（修复 1a / 1b / 2）。

覆盖：
- _first_next() default_branch 守卫
- _first_next() git init 兜底
- _first_next() state 字段填充
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline import orchestrator as _orch_mod
from pipeline.orchestrator import Orchestrator
from pipeline.snapshot import load_snapshot
import bootstrap


def _setup_test_repo(tmp_root: str) -> None:
    """在 tmp_root 初始化一个 git repo + master 分支 + 一次 commit。"""
    # git init 默认创建 master（环境变量 GITHUB_ACTIONS 等可能影响 default branch）
    env = os.environ.copy()
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=tmp_root, check=True, env=env)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_root, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_root, check=True,
    )
    (Path(tmp_root) / "README.md").write_text("init")
    subprocess.run(["git", "add", "-A"], cwd=tmp_root, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "-q"],
        cwd=tmp_root, check=True,
    )
    # 确认在 master
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=tmp_root, capture_output=True, text=True, check=True,
    )
    assert r.stdout.strip() == "master", f"expected master, got {r.stdout.strip()}"


def _clean_worktree(tmp_root: str) -> None:
    """清空未提交的本地变更，确保后续 git init 在干净工作区上。

    pg-build auto_commit_on_init 会 commit 任何 dirty changes，
    这个 helper 让测试明确控制 init_committed 的预期值。
    """
    # 删除 master 上新增的文件（如果还在），checkout 到 master
    subprocess.run(
        ["git", "checkout", "-q", "master"],
        cwd=tmp_root, capture_output=True,
    )
    # 删除 README.md 之外的所有文件（如果有）
    r = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_root, capture_output=True, text=True, check=True,
    )
    if r.stdout.strip():
        # 有未提交内容 → reset
        subprocess.run(
            ["git", "reset", "--hard", "HEAD", "-q"],
            cwd=tmp_root, check=True,
        )
        subprocess.run(
            ["git", "clean", "-fd"],
            cwd=tmp_root, check=True,
        )


def _write_project_yaml(tmp_root: str, default_branch: str = "master") -> None:
    """在 tmp_root/.pg/project.yaml 写 git.default_branch 配置。"""
    pg_dir = os.path.join(tmp_root, ".pg")
    os.makedirs(pg_dir, exist_ok=True)
    project_yaml = os.path.join(pg_dir, "project.yaml")
    with open(project_yaml, "w", encoding="utf-8") as f:
        f.write(f"git:\n  default_branch: {default_branch}\n")


def _write_minimal_manifest(change_root: str) -> None:
    """写最小可用的 execution-manifest.yaml，让 _first_next() 不抛异常。"""
    manifest_path = os.path.join(change_root, "execution-manifest.yaml")
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("""
stages:
  - name: dev
    environment: dev-local
    tracks:
      - id: backend
""")


def _checkout_branch(tmp_root: str, branch: str) -> None:
    """在 tmp_root 内 checkout branch（不存在则创建）。"""
    r = subprocess.run(
        ["git", "checkout", "-q", branch],
        cwd=tmp_root, capture_output=True, text=True,
    )
    if r.returncode != 0:
        subprocess.run(
            ["git", "checkout", "-q", "-b", branch],
            cwd=tmp_root, check=True,
        )


class TestFirstNextBranchAssertion(unittest.TestCase):
    """_first_next() default_branch 守卫测试（修复 1a）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # 设置 PG_PROJECT_ROOT 引导 bootstrap.find_project_root
        self.old_root = os.environ.get("PG_PROJECT_ROOT")
        os.environ["PG_PROJECT_ROOT"] = self.tmp
        bootstrap.PROJECT_ROOT = self.tmp
        bootstrap.CHANGES_DIR = os.path.join(self.tmp, ".pg", "changes")
        # orchestrator.CHANGES_DIR 是模块级常量，需同步修改
        _orch_mod.CHANGES_DIR = bootstrap.CHANGES_DIR

        # 创建 git repo + master 分支
        _setup_test_repo(self.tmp)
        # 写 project.yaml
        _write_project_yaml(self.tmp, default_branch="master")
        # 创建 change 目录
        self.change = "test-branch-assert"
        self.change_root = os.path.join(self.tmp, ".pg", "changes", self.change)
        os.makedirs(self.change_root, exist_ok=True)
        _write_minimal_manifest(self.change_root)

    def tearDown(self):
        if self.old_root:
            os.environ["PG_PROJECT_ROOT"] = self.old_root
        else:
            os.environ.pop("PG_PROJECT_ROOT", None)

    def _build_orchestrator(self) -> Orchestrator:
        """构造 Orchestrator 实例（指向 test change）"""
        orch = Orchestrator(self.change)
        # Orchestrator 内部使用 bootstrap.CHANGES_DIR，所以 .change_root 自动正确
        return orch

    def test_workflow_failed_on_vxlan_branch(self):
        """当前在 vxlan 分支（非 default 非 feat）→ workflow_failed"""
        _checkout_branch(self.tmp, "vxlan")
        orch = self._build_orchestrator()
        result = orch._first_next()

        self.assertEqual(result["action"], "workflow_failed")
        self.assertTrue(result["fatal"])
        self.assertEqual(result["error_category"], "branch_mismatch")
        self.assertEqual(result["current_branch"], "vxlan")
        self.assertEqual(result["expected_branch"], "master")
        self.assertIn("vxlan", result["reason"])
        self.assertIn("master", result["reason"])

        # state 应持久化 failed 状态
        loaded = load_snapshot(self.change_root)
        self.assertEqual(loaded.status, "failed")
        self.assertEqual(loaded.failed_reason, result["reason"])

    def test_passes_on_master_branch(self):
        """当前在 master 分支 → 正常初始化（应返回 env_switch 或 dispatch）"""
        _checkout_branch(self.tmp, "master")
        orch = self._build_orchestrator()
        result = orch._first_next()

        # 正常路径不应返回 workflow_failed
        self.assertNotEqual(result.get("action"), "workflow_failed")
        # state 应为 running
        loaded = load_snapshot(self.change_root)
        self.assertEqual(loaded.status, "running")

    def test_passes_on_existing_feat_branch(self):
        """当前在 feat/pg/<change> 分支 → 正常初始化（resume 场景）"""
        # 先切到 master 创建 feat 分支
        _checkout_branch(self.tmp, "master")
        feat_branch = f"feat/pg/{self.change}"
        _checkout_branch(self.tmp, feat_branch)

        orch = self._build_orchestrator()
        result = orch._first_next()

        # 不应触发 workflow_failed
        self.assertNotEqual(result.get("action"), "workflow_failed")
        loaded = load_snapshot(self.change_root)
        self.assertEqual(loaded.status, "running")

    def test_failed_state_persisted(self):
        """vxlan 触发失败后, reload snapshot 仍为 failed"""
        _checkout_branch(self.tmp, "vxlan")
        orch = self._build_orchestrator()
        orch._first_next()

        # reload
        loaded = load_snapshot(self.change_root)
        self.assertEqual(loaded.status, "failed")
        self.assertIsNotNone(loaded.failed_reason)
        self.assertIn("vxlan", loaded.failed_reason)


class TestFirstNextGitInit(unittest.TestCase):
    """_first_next() git init 兜底测试（修复 1b + 2）。

    验证：
    - _first_next() 在 master 分支上调用时调 ensure_feature_branch + auto_commit_on_init
    - state.feature_branch / init_committed / init_commit_sha 字段被填充
    - init_committed=False 时不写 EVT_GIT_COMMIT 事件
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old_root = os.environ.get("PG_PROJECT_ROOT")
        os.environ["PG_PROJECT_ROOT"] = self.tmp
        bootstrap.PROJECT_ROOT = self.tmp
        bootstrap.CHANGES_DIR = os.path.join(self.tmp, ".pg", "changes")
        _orch_mod.CHANGES_DIR = bootstrap.CHANGES_DIR

        _setup_test_repo(self.tmp)
        _write_project_yaml(self.tmp, default_branch="master")

        self.change = "test-git-init"
        self.change_root = os.path.join(self.tmp, ".pg", "changes", self.change)
        os.makedirs(self.change_root, exist_ok=True)
        _write_minimal_manifest(self.change_root)
        _checkout_branch(self.tmp, "master")

    def tearDown(self):
        if self.old_root:
            os.environ["PG_PROJECT_ROOT"] = self.old_root
        else:
            os.environ.pop("PG_PROJECT_ROOT", None)

    def test_first_next_creates_feature_branch(self):
        """master 分支 → _first_next 创建 feat/pg/<change> 并持久化 feature_branch"""
        orch = Orchestrator(self.change)
        result = orch._first_next()

        # 验证 git 状态：当前在 feat 分支
        r = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=self.tmp, capture_output=True, text=True, check=True,
        )
        self.assertEqual(r.stdout.strip(), f"feat/pg/{self.change}")

        # state 应包含 feature_branch
        loaded = load_snapshot(self.change_root)
        self.assertEqual(loaded.feature_branch, f"feat/pg/{self.change}")

    def test_first_next_state_fields_on_clean_master(self):
        """master 分支 + 工作区干净 → state.init_committed=False, init_commit_sha=None

        Scenario: master 上有 README.md（已 commit），新建 feat/pg/<change> 从 master 分叉，
        新分支 base 包含 README，所以工作区是干净的 → auto_commit_on_init 返回 committed=False。
        """
        # 让 master 上无任何 dirty changes（包括 untracked 文件，如 .pg/）
        # 把整个 tmp 目录纳入 git 管理，确保 "工作区干净" 是真正的干净
        subprocess.run(
            ["git", "add", "-A"], cwd=self.tmp, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "fixture-include-pg", "--allow-empty", "-q"],
            cwd=self.tmp, check=True,
        )
        r = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.tmp, capture_output=True, text=True, check=True,
        )
        self.assertEqual(r.stdout.strip(), "", f"master should be clean: {r.stdout}")

        orch = Orchestrator(self.change)
        result = orch._first_next()
        self.assertNotEqual(result.get("action"), "workflow_failed")

        loaded = load_snapshot(self.change_root)
        self.assertEqual(loaded.feature_branch, f"feat/pg/{self.change}")
        # init_committed 应为 False（feat 分支从 master 分叉带所有内容，无 dirty changes）
        self.assertFalse(loaded.init_committed)
        self.assertIsNone(loaded.init_commit_sha)

    def test_first_next_persists_init_committed_when_already_true(self):
        """orchestrator 状态已 init_committed=True 时跳过 auto_commit_on_init"""
        # 先运行一次，让 state 写入
        orch = Orchestrator(self.change)
        orch._first_next()

        # 重新加载 snapshot
        loaded = load_snapshot(self.change_root)
        # 设置 init_committed=True 后再次调用
        new_state = loaded.replace(init_committed=True, init_commit_sha="abc123")
        from pipeline.snapshot import save_snapshot
        save_snapshot(self.change_root, new_state)

        # mock auto_commit_on_init 验证不再调用
        from unittest.mock import patch
        with patch.object(bootstrap, "auto_commit_on_init") as mock_commit:
            orch2 = Orchestrator(self.change)
            orch2._first_next()

            # init_committed 已为 True → 不应调用 auto_commit_on_init
            mock_commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()