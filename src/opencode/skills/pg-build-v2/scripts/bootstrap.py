"""Bootstrap — pipeline 启动副作用。

5 个步骤（与旧 pg_build_bootstrap 行为对等）：
  1. migrate_legacy_state_files — 创建 2-build/，迁移遗留 state 文件
  2. _ensure_context_chain — 创建 context-chain.md
  3. _ensure_feature_branch — git checkout -b feat/pg/<change>
  4. _maybe_bootstrap_init_commit — git add -A + commit（仅首次）
  5. execute_env_hook_inline — 运行 prepare_env 脚本（v2 内联）

所有步骤容错：失败写 event log 但不阻塞 dispatch。
env-hook 是唯一可能抛出异常（EnvHookError）的步骤。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


# Shanghai timezone
_SHANGHAI = timezone(timedelta(hours=8))


def _now_iso() -> str:
    return datetime.now(_SHANGHAI).strftime("%Y-%m-%dT%H:%M:%S+08:00")


class EnvHookError(Exception):
    """prepare_env 执行失败时抛出。"""
    def __init__(self, phase_name: str, log_path: str, exit_code: int):
        self.phase_name = phase_name
        self.log_path = log_path
        self.exit_code = exit_code
        super().__init__(f"env-hook {phase_name} failed (exit_code={exit_code}, log={log_path})")


def find_project_root() -> str:
    """从 CWD、PG_PROJECT_ROOT 或脚本位置向上查找 .pg/project.yaml。"""
    env_root = os.environ.get("PG_PROJECT_ROOT")
    if env_root and os.path.isfile(os.path.join(env_root, ".pg", "project.yaml")):
        return env_root
    start = os.getcwd()
    cur = os.path.abspath(start)
    for _ in range(8):
        if os.path.isfile(os.path.join(cur, ".pg", "project.yaml")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return start


PROJECT_ROOT = find_project_root()
CHANGES_DIR = os.path.join(PROJECT_ROOT, ".pg", "changes")
APPLY_DIR = "2-build"


# ============================================================
# 步骤 1: 迁移遗留文件
# ============================================================

def migrate_legacy_state_files(change: str) -> list[str]:
    """把 change 根目录遗留的 .pipeline-state.json 等移到 2-build/。

    Args:
        change: change name（从 CHANGES_DIR 拼接路径）

    Returns:
        list of moved file descriptions
    """
    return _migrate_files_impl(os.path.join(CHANGES_DIR, change))


def _migrate_files_impl(change_root: str) -> list[str]:
    """内部实现：接受显式 change_root 路径。"""
    apply_dir = os.path.join(change_root, APPLY_DIR)
    moved: list[str] = []
    legacy_files = [".pipeline-state.json", ".context-chain.state"]

    os.makedirs(apply_dir, exist_ok=True)

    for fname in legacy_files:
        legacy = os.path.join(change_root, fname)
        target = os.path.join(apply_dir, fname)
        if not os.path.isfile(legacy):
            continue
        if os.path.isfile(target):
            os.remove(legacy)
            moved.append(f"{fname} (legacy removed, target existed)")
        else:
            os.rename(legacy, target)
            moved.append(fname)
    return moved


# ============================================================
# 步骤 2: 确保 context-chain.md 存在
# ============================================================

def ensure_context_chain(change: str) -> None:
    """创建 context-chain.md 文件（如果不存在）。"""
    path = os.path.join(CHANGES_DIR, change, APPLY_DIR, "context-chain.md")
    if os.path.isfile(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Context Chain - {change}\n\n")
        f.write("---\n")
        f.write("*此文件由编排器自动管理，请勿手动修改*\n\n")
        f.write(f"### {_now_iso()} - PIPELINE STARTED\n")
        f.write("**状态**: INITIALIZED\n\n")


# ============================================================
# 步骤 3: 确保 feature branch
# ============================================================

def _git(*args: str, capture: bool = True) -> subprocess.CompletedProcess:
    """执行 git 命令。"""
    kwargs = {"cwd": PROJECT_ROOT}
    if capture:
        kwargs["capture_output"] = True
        kwargs["text"] = True
    return subprocess.run(["git", *args], **kwargs)


def ensure_feature_branch(change: str) -> dict[str, Any]:
    """创建 feat/pg/{change} 分支（如果不在该分支上）。

    Returns:
        {"branch": "...", "action": "created|checked_out|already_on"}
    """
    expected = f"feat/pg/{change}"
    branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    if branch == expected:
        return {"branch": expected, "action": "already_on"}

    _git("stash", capture=True)
    r = _git("rev-parse", "--verify", expected)
    if r.returncode == 0:
        _git("checkout", expected)
        return {"branch": expected, "action": "checked_out"}
    else:
        _git("checkout", "-b", expected, branch)
        return {"branch": expected, "action": "created"}


# ============================================================
# 步骤 4: init commit
# ============================================================

def auto_commit_on_init(change: str) -> dict[str, Any]:
    """执行 bootstrap init commit。

    Returns:
        {"attempted": bool, "committed": bool, "sha": str|None, "message": str, "reason": str|None}
    """
    # 检查工作区是否干净
    status = _git("status", "--porcelain").stdout.strip()
    if not status:
        return {
            "attempted": True,
            "committed": False,
            "sha": None,
            "message": "",
            "reason": "工作区干净，无可提交内容（init 阶段）",
        }

    _git("add", "-A")
    msg = f"chore({change}): bootstrap pg-build"
    r = _git("commit", "-m", msg)
    if r.returncode == 0:
        sha = _git("rev-parse", "HEAD").stdout.strip()
        return {
            "attempted": True,
            "committed": True,
            "sha": sha,
            "message": msg,
            "reason": None,
        }
    return {
        "attempted": True,
        "committed": False,
        "sha": None,
        "message": msg,
        "reason": r.stderr.strip() or "commit failed",
    }


def maybe_bootstrap_init_commit(change: str, init_committed: bool) -> dict[str, Any] | None:
    """仅首次执行 init commit。

    Args:
        change: change name
        init_committed: 是否已经执行过

    Returns:
        init_commit 结果 dict（仅首次返回），后续返回 None
    """
    if init_committed:
        return None
    return auto_commit_on_init(change)


# ============================================================
# 步骤 5: prepare_env 内联执行
# ============================================================

def execute_env_hook_inline(change: str, phase_name: str = "prepare_env") -> dict[str, Any]:
    """执行 prepare_env 脚本。

    Returns:
        {"success": bool, "skipped": bool, "log_path": str|None, "exit_code": int|None, ...}
    """
    if phase_name not in ("prepare_env", "clean_env"):
        return {"success": False, "skipped": False, "error": f"invalid phase: {phase_name}"}

    # 读取 project.yaml 获取 env 配置
    config_path = os.path.join(PROJECT_ROOT, ".pg", "project.yaml")
    if not os.path.isfile(config_path):
        return {"success": True, "skipped": True, "error": None}
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception as e:
        return {"success": False, "skipped": False, "error": f"load_config failed: {e}"}

    # 找第一个有 environment.required=true 的 stage
    env_name = None
    stage_name = None
    for s in config.get("stages") or []:
        if s.get("tracks") and (s.get("environment") or {}).get("required", False):
            stage_name = s.get("name")
            env_name = s.get("environment", {}).get("name")
            break

    if not env_name:
        return {"success": True, "skipped": True}

    # 读取 environment.yaml 获取实际 env 映射
    env_yaml_path = os.path.join(CHANGES_DIR, change, "environment.yaml")
    if os.path.isfile(env_yaml_path):
        try:
            with open(env_yaml_path, encoding="utf-8") as f:
                env_map = yaml.safe_load(f) or {}
            mapped = env_map.get(stage_name, env_name)
            if mapped == "skip":
                return {"success": True, "skipped": True}
            if mapped:
                env_name = mapped
        except Exception:
            pass

    env_cfg = (config.get("environments") or {}).get(env_name, {})
    action = env_cfg.get(phase_name)
    if not action:
        return {"success": True, "skipped": True}

    script_path = action.get("script")
    if not script_path:
        return {"success": False, "skipped": False,
                "error": f"environment {env_name}.{phase_name} has no script"}

    if not os.path.isabs(script_path):
        script_path = os.path.join(PROJECT_ROOT, script_path)

    # 构建命令
    args = action.get("args") or []
    cmd = f"bash {script_path}" + (" " + " ".join(str(a) for a in args) if args else "")

    # 日志路径
    log_path = os.path.join(
        CHANGES_DIR, change, APPLY_DIR,
        f"{phase_name}-{_now_iso().replace(':', '-')}.log"
    )

    # 执行
    timeout = action.get("timeout_seconds") or 600
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        proc = subprocess.run(
            cmd, shell=True,
            stdout=open(log_path, "w"),
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        exit_code = 124
        with open(log_path, "a") as f:
            f.write(f"\nTIMEOUT after {timeout}s\n")
    except Exception as e:
        exit_code = 1
        with open(log_path, "a") as f:
            f.write(f"\nEXCEPTION: {e}\n")

    success = (exit_code == 0)
    return {
        "success": success,
        "skipped": False,
        "log_path": log_path,
        "exit_code": exit_code,
        "env_name": env_name,
        "phase_item": f"{stage_name}.{phase_name}",
        "error": None if success else f"exit_code={exit_code}, log={log_path}",
    }


# ============================================================
# 启动入口：run_bootstrap
# ============================================================

def run_bootstrap(
    change: str,
    init_committed: bool = False,
    event_log=None,
) -> dict[str, Any]:
    """执行完整的 5 步 bootstrap。

    Args:
        change: change name
        init_committed: 是否已 init commit（从 state 读取）
        event_log: EventLog 实例（可选，写入 bootstrap 事件）

    Returns:
        {"ok": bool, "init_commit": dict|None, "env_hook": dict|None,
         "prepare_env_failed": bool, "prepare_env_log_path": str|None}
    """
    result: dict[str, Any] = {
        "ok": True,
        "init_commit": None,
        "env_hook": None,
        "prepare_env_failed": False,
        "prepare_env_log_path": None,
    }

    def _log(event_type: str, data: dict) -> None:
        if event_log is not None:
            try:
                event_log.append(event_type, data)
            except Exception:
                pass

    # 步骤 1: migrate
    try:
        moved = migrate_legacy_state_files(change)
        if moved:
            _log("bootstrap_step_completed", {"step": 1, "detail": f"migrated: {moved}"})
    except Exception as e:
        _log("bootstrap_step_completed", {"step": 1, "error": str(e)})

    # 步骤 2: context-chain
    try:
        ensure_context_chain(change)
        _log("bootstrap_step_completed", {"step": 2})
    except Exception as e:
        _log("bootstrap_step_completed", {"step": 2, "error": str(e)})

    # 步骤 3: feature branch
    try:
        branch_result = ensure_feature_branch(change)
        _log("bootstrap_step_completed", {"step": 3, "detail": branch_result})
    except Exception as e:
        _log("bootstrap_step_completed", {"step": 3, "error": str(e)})

    # 步骤 4: init commit
    try:
        init_commit = maybe_bootstrap_init_commit(change, init_committed)
        if init_commit is not None:
            result["init_commit"] = init_commit
            _log("git_commit", {
                "sha": init_commit.get("sha"),
                "message": init_commit.get("message", ""),
                "branch": f"feat/pg/{change}",
            })
    except Exception as e:
        _log("bootstrap_step_completed", {"step": 4, "error": str(e)})

    # 步骤 5: prepare_env
    try:
        env_result = execute_env_hook_inline(change, "prepare_env")
        result["env_hook"] = env_result
        if env_result.get("log_path"):
            result["prepare_env_log_path"] = env_result["log_path"]

        if not env_result.get("success"):
            result["prepare_env_failed"] = True
            if not env_result.get("skipped"):
                result["ok"] = False
                _log("prepare_env_completed", {
                    "success": False, "exit_code": env_result.get("exit_code"),
                    "log_path": env_result.get("log_path"),
                })
                raise EnvHookError(
                    phase_name="prepare_env",
                    log_path=env_result.get("log_path") or "",
                    exit_code=env_result.get("exit_code") or -1,
                )
        else:
            _log("prepare_env_completed", {
                "success": True, "skipped": env_result.get("skipped", False),
                "log_path": env_result.get("log_path"),
            })
    except EnvHookError:
        raise
    except Exception as e:
        _log("bootstrap_step_completed", {"step": 5, "error": str(e)})

    return result