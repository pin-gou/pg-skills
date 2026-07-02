"""Bootstrap — pipeline 启动副作用。

4 个步骤（v2.1 起 context-chain.md 被 pipeline.events 取代）：
  1. migrate_legacy_state_files — 创建 2-build/，迁移遗留 state 文件
  2. _ensure_feature_branch — git checkout -b feat/pg/<change>
  3. _maybe_bootstrap_init_commit — git add -A + commit（仅首次）
  4. execute_env_hook_inline — 运行 prepare_env 脚本（v2 内联）

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
    def __init__(self, phase_name: str, log_path: str, exit_code: int,
                 error_category: str = "", error_message: str = "", error_hint: str = ""):
        self.phase_name = phase_name
        self.log_path = log_path
        self.exit_code = exit_code
        self.error_category = error_category
        self.error_message = error_message
        self.error_hint = error_hint
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
# 步骤 2: （已删除 — v2.1 起 context-chain.md 被 pipeline.events 取代）
# ============================================================


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

def execute_env_hook_inline(
    change: str,
    phase_name: str = "prepare_env",
    explicit_env_name: str | None = None,
    explicit_stage_name: str | None = None,
) -> dict[str, Any]:
    """执行 prepare_env / clean_env 脚本。

    Args:
        change: change name
        phase_name: "prepare_env" | "clean_env"
        explicit_env_name: 显式 env 名（来自 env_switch action detail，跳过自动检测）
        explicit_stage_name: 显式 stage 名（同上）

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

    # 确定 env_name / stage_name
    # 优先级: 显式参数 > manifest 自动检测 > project.yaml fallback
    env_name = explicit_env_name
    stage_name = explicit_stage_name

    if not env_name:
        # 从 execution-manifest.yaml 读取 env 名
        manifest_path = os.path.join(CHANGES_DIR, change, "execution-manifest.yaml")
        if os.path.isfile(manifest_path):
            try:
                import yaml as _yaml2
                with open(manifest_path, encoding="utf-8") as f:
                    manifest = _yaml2.safe_load(f) or {}
                for s in manifest.get("stages", []):
                    env = s.get("environment")
                    if env and s.get("tracks"):
                        stage_name = s.get("name", "")
                        env_name = env if isinstance(env, str) else env.get("name", "")
                        if env_name:
                            break
            except Exception:
                pass

    if not env_name:
        # fallback: 从 project.yaml stages 读取 env 名
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
    result_file = os.path.join(
        CHANGES_DIR, change, APPLY_DIR,
        f"{phase_name}-result.json"
    )
    hook_log_dir = os.path.join(CHANGES_DIR, change, APPLY_DIR, "logs")

    # 构建 hooks 协议环境变量（与 pg-run-hook.py 的 build_env() 一致）
    _env = os.environ.copy()
    _env.setdefault("PG_PROJECT_ROOT", PROJECT_ROOT)
    _env.setdefault("PG_SKILLS_PATH", os.path.join(PROJECT_ROOT, ".pg", "skills"))
    _env.setdefault("PG_RUN_CALLER", "pg-build-v2")
    _env["PG_ENV"] = env_name
    _env["PG_STAGE"] = stage_name or ""
    _env["PG_HOOK_TYPE"] = phase_name
    _env["PG_HOOK_LOG_DIR"] = hook_log_dir
    _env["PG_LOG_FILE"] = log_path
    _env["PG_RESULT_FILE"] = result_file

    # 执行
    timeout = action.get("timeout_seconds") or 600
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        proc = subprocess.run(
            cmd, shell=True,
            stdout=open(log_path, "w"),
            stderr=subprocess.STDOUT,
            timeout=timeout, env=_env,
            cwd=PROJECT_ROOT,
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
    result: dict[str, Any] = {
        "success": success,
        "skipped": False,
        "log_path": log_path,
        "exit_code": exit_code,
        "env_name": env_name,
        "phase_item": f"{stage_name}.{phase_name}",
        "error": None if success else f"exit_code={exit_code}, log={log_path}",
        "hook_result": None,
    }

    # 读取 hook 写入的 result.json（hooks 协议要求）
    if os.path.isfile(result_file):
        try:
            with open(result_file, encoding="utf-8") as f:
                hook_result = json.load(f)
            result["hook_result"] = hook_result
            if not success and hook_result.get("error"):
                err = hook_result["error"]
                result["error"] = json.dumps(err, ensure_ascii=False)
                result["error_category"] = err.get("category", "unknown")
                result["error_message"] = err.get("message", "")
                result["error_hint"] = err.get("hint", "")
        except Exception as e:
            result["hook_result_error"] = str(e)

    return result


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

    # 步骤 2: （已删除 — v2.1 起 context-chain.md 被 pipeline.events 取代）

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
                    "error_category": env_result.get("error_category", "unknown"),
                    "error_message": env_result.get("error_message", ""),
                    "error_hint": env_result.get("error_hint", ""),
                })
                raise EnvHookError(
                    phase_name="prepare_env",
                    log_path=env_result.get("log_path") or "",
                    exit_code=env_result.get("exit_code") or -1,
                    error_category=env_result.get("error_category", "unknown"),
                    error_message=env_result.get("error_message", ""),
                    error_hint=env_result.get("error_hint", ""),
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


# ============================================================
# CLI 入口：cli_bootstrap / cli_env_action
# ============================================================

def _detect_pipeline_config_from_disk(change: str) -> dict[str, Any]:
    """从 execution-manifest.yaml + project.yaml 检测 pipeline 配置。

    纯文件系统操作，与 Orchestrator.state 无关。
    供 cli_bootstrap 命令使用。

    Returns:
        {"pipeline_order": [...], "track_configs": {...}, "stage_order": [...], "stage_env_map": {...}}
    """
    from pipeline.events import FINAL_GATE_TRACK
    order: list[str] = []
    track_configs: dict[str, dict] = {}
    stage_order: list[str] = []
    stage_env_map: dict[str, str] = {}
    config_path = os.path.join(PROJECT_ROOT, ".pg", "project.yaml")

    # 从 execution-manifest.yaml 读 stage order + env 映射
    manifest_path = os.path.join(CHANGES_DIR, change, "execution-manifest.yaml")
    if os.path.isfile(manifest_path):
        try:
            import yaml as _yaml
            with open(manifest_path, encoding="utf-8") as f:
                manifest = _yaml.safe_load(f) or {}
            for stage in manifest.get("stages", []):
                name = stage.get("name", "")
                if not name:
                    continue
                stage_order.append(name)
                env = stage.get("environment", "")
                if isinstance(env, str):
                    stage_env_map[name] = env
                elif isinstance(env, dict):
                    stage_env_map[name] = env.get("name", "dev-local")
                else:
                    stage_env_map[name] = "dev-local"
                for track in stage.get("tracks", []):
                    tid = track["id"] if isinstance(track, dict) else track
                    qualified = f"{name}.{tid}" if name else tid
                    order.append(qualified)
                    if isinstance(track, dict) and track.get("commands"):
                        track_configs[qualified] = {"commands": track["commands"]}
            if manifest.get("final_gate"):
                order.append(FINAL_GATE_TRACK)
        except Exception:
            pass

    # fallback: project.yaml
    if not order and os.path.isfile(config_path):
        try:
            import yaml as _yaml
            with open(config_path, encoding="utf-8") as f:
                config = _yaml.safe_load(f) or {}
            for stage in config.get("stages", []):
                stage_name = stage.get("name", "")
                stage_order.append(stage_name)
                env = stage.get("environment", {})
                if isinstance(env, dict) and env.get("name"):
                    stage_env_map[stage_name] = env["name"]
                for t in stage.get("tracks", []):
                    qualified = f"{stage_name}.{t}" if stage_name else t
                    order.append(qualified)
        except Exception:
            pass

    if not stage_order:
        stage_order.append("dev")

    # 从 project.yaml 读 track 级配置
    if os.path.isfile(config_path):
        try:
            import yaml as _yaml
            with open(config_path, encoding="utf-8") as f:
                config = _yaml.safe_load(f) or {}
            tracks_cfg = config.get("tracks", {})
            for tid in order:
                if tid == FINAL_GATE_TRACK:
                    continue
                bare = tid.rsplit(".", 1)[-1]
                if tid not in track_configs:
                    track_configs[tid] = {}
                cfg = tracks_cfg.get(bare, {})
                track_configs[tid].setdefault("modules", cfg.get("modules", []))
                track_configs[tid].setdefault("max_fail_retries", cfg.get("max_fail_retries", 3))
                track_configs[tid].setdefault("max_fix_retries", cfg.get("max_fix_retries", 5))
                track_configs[tid].setdefault("max_gate_fix_retries", cfg.get("max_gate_fix_retries", 2))
                track_configs[tid].setdefault("type", cfg.get("type", "standard"))
                track_configs[tid].setdefault("review_level", cfg.get("review_level", ""))
                track_configs[tid].setdefault("description", cfg.get("description", ""))
                track_configs[tid].setdefault("fix_routing", cfg.get("fix_routing", "source"))
        except Exception:
            pass

    return {
        "pipeline_order": order,
        "track_configs": track_configs,
        "stage_order": stage_order,
        "stage_env_map": stage_env_map,
    }


def cli_bootstrap(change: str) -> dict[str, Any]:
    """CLI 入口：执行 bootstrap 5 步 + 检测 pipeline 配置。

    与 Orchestrator._first_next 中的 bootstrap 逻辑等价，
    但独立于 PipelineState 和 EventLog，适合 CLI 调用。

    Returns:
        {"action": "bootstrap_result", "ok": bool, "init_commit": dict|None,
         "env_hook": dict|None, "pipeline_config": dict|None,
         "error": str|None}
    """
    result: dict[str, Any] = {
        "action": "bootstrap_result",
        "ok": True,
        "init_commit": None,
        "env_hook": None,
        "pipeline_config": None,
        "error": None,
        "prepare_env_failed": False,
        "prepare_env_log_path": None,
    }

    # 步骤 1-4: migrate, feature branch, init commit
    try:
        moved = migrate_legacy_state_files(change)
    except Exception as e:
        result["error"] = f"migrate failed: {e}"

    try:
        branch_result = ensure_feature_branch(change)
    except Exception as e:
        result["error"] = f"branch failed: {e}"

    # init commit: 从 state 读取 init_committed 状态。CLI 模式无 state，
    # 每次尝试提交（幂等：git commit 在工作区干净时跳过）
    try:
        init_commit = auto_commit_on_init(change)
        if init_commit.get("committed") or init_commit.get("reason"):
            result["init_commit"] = init_commit
    except Exception as e:
        result["error"] = f"init_commit failed: {e}"

    # 步骤 5: prepare_env
    try:
        env_result = execute_env_hook_inline(change, "prepare_env")
        result["env_hook"] = env_result
        if env_result.get("log_path"):
            result["prepare_env_log_path"] = env_result["log_path"]
        if not env_result.get("success") and not env_result.get("skipped"):
            result["prepare_env_failed"] = True
            result["ok"] = False
            result["error"] = env_result.get("error") or f"prepare_env failed (exit_code={env_result.get('exit_code')})"
    except Exception as e:
        result["prepare_env_failed"] = True
        result["ok"] = False
        result["error"] = f"prepare_env exception: {e}"

    # 检测 pipeline 配置（不依赖 Orchestrator）
    try:
        pipeline_config = _detect_pipeline_config_from_disk(change)
        result["pipeline_config"] = pipeline_config
    except Exception as e:
        result["error"] = f"detect config failed: {e}"

    return result


def cli_env_action(change: str, phase_name: str, stage_name: str, env_name: str) -> dict[str, Any]:
    """CLI 入口：执行一次 env hook（prepare_env / clean_env）。

    与 Orchestrator._handle_env_switch 中的执行逻辑等价，
    但独立于 PipelineState，适合 CLI 调用。

    Args:
        change: change name
        phase_name: "prepare_env" | "clean_env"
        stage_name: 当前 stage 名称
        env_name: 环境名称

    Returns:
        {"action": "env_action_result", "ok": bool, ...}
    """
    result: dict[str, Any] = {
        "action": "env_action_result",
        "ok": False,
        "phase": phase_name,
        "stage": stage_name,
        "env_name": env_name,
        "log_path": None,
        "exit_code": None,
        "error": None,
    }

    try:
        env_result = execute_env_hook_inline(
            change, phase_name,
            explicit_env_name=env_name,
            explicit_stage_name=stage_name,
        )
        result["log_path"] = env_result.get("log_path")
        result["exit_code"] = env_result.get("exit_code")
        result["error_category"] = env_result.get("error_category", "unknown")
        result["error_message"] = env_result.get("error_message", "")
        result["error_hint"] = env_result.get("error_hint", "")

        if env_result.get("success"):
            result["ok"] = True
        elif env_result.get("skipped"):
            result["ok"] = True
            result["skipped"] = True
        else:
            result["error"] = env_result.get("error") or f"{phase_name} failed"
    except Exception as e:
        result["error"] = f"{phase_name} exception: {e}"

    return result