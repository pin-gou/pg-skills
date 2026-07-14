"""Bootstrap — pipeline 启动副作用。

v2.1.1 重构：
  - cli_bootstrap / cli_env_action 改为只返回 plan，不执行 env hook。
  - 编排器按 plan 自己 bash 执行 env hook（解决 LLM 端 bash timeout
    截断 prepare_env 内部 timeout 的问题）。
  - 编排器执行完调 cli_env_action_result 写 *_COMPLETED event + 更新 state。

5 个步骤：
  1. migrate_legacy_state_files — 创建 2-build/，迁移遗留 state 文件
  2. ensure_feature_branch — git checkout -b feat/pg/<change>
  3. auto_commit_on_init — git add -A + commit（仅首次）
  4. cli_bootstrap 解析 env_hook_plan（不执行）/ cli_env_action 解析 plan
  5. cli_env_action_result 写 completed event + 更新 stage_prepared/current_stage

所有步骤容错：失败写 event log 但不阻塞 dispatch。
env-hook 是唯一可能抛出异常（EnvHookError）的步骤。
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

# v2.1: event log 写入需要
from pipeline.events import (
    EVT_PREPARE_ENV_STARTED, EVT_PREPARE_ENV_COMPLETED,
    EVT_CLEAN_ENV_STARTED, EVT_CLEAN_ENV_COMPLETED,
)
from pipeline.event_log import EventLog


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
    """把 change 根目录遗留的 .pipeline-state.json 等移到 2-build/。"""
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
    """创建 feat/pg/{change} 分支（如果不在该分支上）。"""
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
    """执行 bootstrap init commit。"""
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
    """仅首次执行 init commit。"""
    if init_committed:
        return None
    return auto_commit_on_init(change)


# ============================================================
# 步骤 5: prepare_env / clean_env 计划 + 执行 (v2.1.1 重构)
# ============================================================
#
# v2.1.1 重构：把"解析 env hook 命令"和"执行 env hook 命令"拆开。
#   - _build_env_hook_plan() — 纯函数，从 project.yaml / execution-manifest.yaml
#     解析出 (command, env_name, stage_name, timeout_seconds,
#     log_path, env) 等；不执行。
#   - _execute_plan() — 复用 _build_env_hook_plan() 获取 plan，然后同步执行。
#   - execute_env_hook_inline() — 旧 API 的兼容包装。
#   - cli_bootstrap / cli_env_action 改为只返回 plan，**不执行**。
#     编排器按 plan 自己 bash 执行（这样 env hook 真在 LLM 的 bash timeout
#     下运行，避免 prepare_env 内部 timeout > LLM bash timeout 导致中断）。
#   - cli_env_action_result() — 编排器执行完 bash 后调用，写 *_COMPLETED
#     event + 更新 stage_prepared / current_stage。
# ============================================================


def _build_env_hook_plan(
    change: str,
    phase_name: str,
    explicit_env_name: str | None = None,
    explicit_stage_name: str | None = None,
    explicit_timeout: int | None = None,
) -> dict[str, Any]:
    """解析 env hook 执行计划（纯函数，不执行任何 subprocess）。

    Returns:
        {
          "ok": bool,
          "skipped": bool,
          "command": str,
          "env_name": str,
          "stage_name": str,
          "timeout_seconds": int,
          "log_path": str,
          "result_file": str,
          "hook_log_dir": str,
          "env": dict,
          "error": str|None,
        }
    """
    if phase_name not in ("prepare_env", "clean_env"):
        return {"ok": False, "skipped": False, "error": f"invalid phase: {phase_name}"}

    config_path = os.path.join(PROJECT_ROOT, ".pg", "project.yaml")
    if not os.path.isfile(config_path):
        return {"ok": True, "skipped": True}

    try:
        import yaml as _yaml
        with open(config_path, encoding="utf-8") as f:
            config = _yaml.safe_load(f) or {}
    except Exception as e:
        return {"ok": False, "skipped": False, "error": f"load_config failed: {e}"}

    env_name = explicit_env_name
    stage_name = explicit_stage_name

    if not env_name:
        manifest_path = os.path.join(CHANGES_DIR, change, "execution-manifest.yaml")
        if os.path.isfile(manifest_path):
            try:
                with open(manifest_path, encoding="utf-8") as f:
                    manifest = _yaml.safe_load(f) or {}
                # 关键修复：按 stage 顺序查找，只取匹配 explicit_stage_name 的，
                # 避免"第一个有 env 的 stage"覆盖其他 stage。
                for s in manifest.get("stages", []):
                    sn = s.get("name", "")
                    if explicit_stage_name and sn != explicit_stage_name:
                        continue
                    env = s.get("environment")
                    if env and s.get("tracks"):
                        stage_name = stage_name or sn
                        env_name = env if isinstance(env, str) else env.get("name", "")
                        if env_name:
                            break
            except Exception:
                pass

    if not env_name:
        for s in config.get("stages") or []:
            if explicit_stage_name and s.get("name") != explicit_stage_name:
                continue
            if s.get("tracks") and (s.get("environment") or {}).get("required", False):
                stage_name = stage_name or s.get("name")
                env_name = s.get("environment", {}).get("name")
                break

    if not env_name:
        return {"ok": True, "skipped": True}

    env_cfg = (config.get("environments") or {}).get(env_name, {})
    action = env_cfg.get(phase_name)
    if not action:
        return {"ok": True, "skipped": True}

    script_path = action.get("script")
    if not script_path:
        return {"ok": False, "skipped": False,
                "error": f"environment {env_name}.{phase_name} has no script"}

    if not os.path.isabs(script_path):
        script_path = os.path.join(PROJECT_ROOT, script_path)

    args = action.get("args") or []
    cmd = f"bash {script_path}" + (" " + " ".join(str(a) for a in args) if args else "")

    log_path = os.path.join(
        CHANGES_DIR, change, APPLY_DIR,
        f"{phase_name}-{_now_iso().replace(':', '-')}.log"
    )
    result_file = os.path.join(
        CHANGES_DIR, change, APPLY_DIR,
        f"{phase_name}-result.json"
    )
    hook_log_dir = os.path.join(CHANGES_DIR, change, APPLY_DIR, "logs")

    _env = os.environ.copy()
    _env.setdefault("PG_PROJECT_ROOT", PROJECT_ROOT)
    _env.setdefault("PG_SKILLS_PATH", os.path.join(PROJECT_ROOT, ".pg", "skills"))
    _env.setdefault("PG_RUN_CALLER", "pg-build")
    _env["PG_ENV"] = env_name
    _env["PG_STAGE"] = stage_name or ""
    _env["PG_HOOK_TYPE"] = phase_name
    _env["PG_HOOK_LOG_DIR"] = hook_log_dir
    _env["PG_LOG_FILE"] = log_path
    _env["PG_RESULT_FILE"] = result_file

    timeout = explicit_timeout or action.get("timeout_seconds") or 600

    return {
        "ok": True,
        "skipped": False,
        "command": cmd,
        "env_name": env_name,
        "stage_name": stage_name or "",
        "timeout_seconds": timeout,
        "log_path": log_path,
        "result_file": result_file,
        "hook_log_dir": hook_log_dir,
        "env": _env,
    }


def _execute_plan(plan: dict[str, Any], phase_name: str) -> dict[str, Any]:
    """按 plan 同步执行 env hook。供单测 / 内部使用，CLI 入口不再调用。"""
    if plan.get("skipped"):
        return {"success": True, "skipped": True, "log_path": None, "exit_code": None}

    if not plan.get("ok"):
        return {"success": False, "skipped": False, "error": plan.get("error")}

    cmd = plan["command"]
    log_path = plan["log_path"]
    timeout = plan["timeout_seconds"]
    _env = plan["env"]

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
        "env_name": plan["env_name"],
        "phase_item": f"{plan['stage_name']}.{phase_name}",
        "error": None if success else f"exit_code={exit_code}, log={log_path}",
        "hook_result": None,
    }

    result_file = plan.get("result_file", "")
    if result_file and os.path.isfile(result_file):
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


def execute_env_hook_inline(
    change: str,
    phase_name: str = "prepare_env",
    explicit_env_name: str | None = None,
    explicit_stage_name: str | None = None,
) -> dict[str, Any]:
    """同步执行 env hook（保留旧接口，cli_* 不再调用）。

    新代码应使用 _build_env_hook_plan() 解析，由编排器自行 bash 执行。
    """
    plan = _build_env_hook_plan(
        change, phase_name,
        explicit_env_name=explicit_env_name,
        explicit_stage_name=explicit_stage_name,
    )
    return _execute_plan(plan, phase_name)


# ============================================================
# 启动入口：run_bootstrap
# ============================================================

def run_bootstrap(
    change: str,
    init_committed: bool = False,
    event_log=None,
) -> dict[str, Any]:
    """执行完整的 bootstrap 副作用。

    v2.1.1: prepare_env 不再同步执行。env hook 由编排器在 next() 后调
    env-action-result 推进。
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

    try:
        moved = migrate_legacy_state_files(change)
        if moved:
            _log("bootstrap_step_completed", {"step": 1, "detail": f"migrated: {moved}"})
    except Exception as e:
        _log("bootstrap_step_completed", {"step": 1, "error": str(e)})

    try:
        branch_result = ensure_feature_branch(change)
        _log("bootstrap_step_completed", {"step": 3, "detail": branch_result})
    except Exception as e:
        _log("bootstrap_step_completed", {"step": 3, "error": str(e)})

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

    # 步骤 5: prepare_env — v2.1.1: 不再同步执行

    return result


# ============================================================
# CLI 入口：cli_bootstrap / cli_env_action / cli_env_action_result
# ============================================================

def _detect_pipeline_config_from_disk(change: str) -> dict[str, Any]:
    """从 execution-manifest.yaml + project.yaml 检测 pipeline 配置。

    v3: 严格按 manifest.tracks[].enabled 决定派发顺序。
        enabled=false 的 track 不加入 pipeline_order。
        旧 manifest 缺 enabled 字段时默认禁用（v3 安全策略）。
    """
    from pipeline.events import FINAL_GATE_TRACK
    order: list[str] = []
    track_configs: dict[str, dict] = {}
    stage_order: list[str] = []
    stage_env_map: dict[str, str] = {}
    config_path = os.path.join(PROJECT_ROOT, ".pg", "project.yaml")

    manifest_path = os.path.join(CHANGES_DIR, change, "execution-manifest.yaml")
    manifest = None
    if os.path.isfile(manifest_path):
        try:
            import yaml as _yaml
            with open(manifest_path, encoding="utf-8") as f:
                manifest = _yaml.safe_load(f) or {}
        except Exception:
            manifest = None

    if manifest is not None:
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
                if not isinstance(track, dict):
                    tid = track
                    qualified = f"{name}.{tid}" if name else tid
                    order.append(qualified)
                    continue

                tid = track.get("id", "")
                # v3: 严格按 enabled 决定派发；缺字段时默认禁用
                if "enabled" not in track:
                    print(
                        f"[bootstrap] WARN: track {tid!r} in stage {name!r} "
                        f"manifest 缺 enabled 字段，默认禁用（建议重跑 pg-propose-refine）",
                        file=sys.stderr,
                    )
                    continue
                if not track["enabled"]:
                    continue

                qualified = f"{name}.{tid}" if name else tid
                order.append(qualified)

                tcfg: dict[str, Any] = {}
                if track.get("commands"):
                    tcfg["commands"] = track["commands"]
                if track.get("target_module"):
                    tcfg["target_module"] = track["target_module"]
                if track.get("type"):
                    tcfg["type"] = track["type"]
                track_configs[qualified] = tcfg

        if manifest.get("final_gate"):
            order.append(FINAL_GATE_TRACK)

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
                track_configs[tid].setdefault("description", cfg.get("description", ""))
                track_configs[tid].setdefault("timeout_seconds", cfg.get("timeout_seconds", 1800))
        except Exception:
            pass

    return {
        "pipeline_order": order,
        "track_configs": track_configs,
        "stage_order": stage_order,
        "stage_env_map": stage_env_map,
    }


def _inline_env_into_command(plan: dict[str, Any]) -> None:
    """将 plan.env 中的关键变量内联到 plan.command 头部（env 前缀模式）。

    v2.1.1 重构后编排器自行 bash 执行 env hook，不再通过 subprocess 传 env 字典。
    此函数将 _build_env_hook_plan 构造的 env 覆盖变量注入 command 字符串，
    使编排器执行 bash plan.command 时环境变量自动生效。
    """
    env = plan.get("env") or {}
    override_keys = [
        "PG_PROJECT_ROOT", "PG_SKILLS_PATH", "PG_RUN_CALLER",
        "PG_ENV", "PG_STAGE", "PG_HOOK_TYPE", "PG_HOOK_LOG_DIR",
        "PG_LOG_FILE", "PG_RESULT_FILE",
    ]
    env_parts = []
    for k in override_keys:
        v = env.get(k)
        if v is not None:
            env_parts.append(f"{k}={shlex.quote(str(v))}")
    if env_parts:
        plan["command"] = f"env {' '.join(env_parts)} {plan['command']}"

    log_path = plan.get("log_path", "")
    if log_path:
        plan["command"] += f" > {shlex.quote(log_path)} 2>&1"


def cli_bootstrap(change: str) -> dict[str, Any]:
    """CLI 入口：执行 bootstrap 副作用（不含 env hook）+ 检测 pipeline 配置。

    v2.1.1 重构：
      - bootstrap 不再同步执行 prepare_env。env hook 拆到首次 `next()` 返回的
        `env_switch` action，由编排器按 plan 自己 bash 执行。

    Returns:
        {
          "action": "bootstrap_result",
          "ok": bool,
          "init_commit": dict|None,
          "env_hook_plan": dict|None,
          "pipeline_config": dict|None,
          "error": str|None,
        }
    """
    result: dict[str, Any] = {
        "action": "bootstrap_result",
        "ok": True,
        "init_commit": None,
        "env_hook_plan": None,
        "pipeline_config": None,
        "error": None,
    }

    try:
        moved = migrate_legacy_state_files(change)
    except Exception as e:
        result["error"] = f"migrate failed: {e}"

    try:
        branch_result = ensure_feature_branch(change)
    except Exception as e:
        result["error"] = f"branch failed: {e}"

    try:
        init_commit = auto_commit_on_init(change)
        if init_commit.get("committed") or init_commit.get("reason"):
            result["init_commit"] = init_commit
    except Exception as e:
        result["error"] = f"init_commit failed: {e}"

    try:
        plan = _build_env_hook_plan(change, "prepare_env")
        if not plan.get("ok"):
            result["ok"] = False
            result["error"] = plan.get("error", "plan build failed")
        elif not plan.get("skipped"):
            _inline_env_into_command(plan)
            plan_for_orchestrator = {k: v for k, v in plan.items() if k != "env"}
            result["env_hook_plan"] = plan_for_orchestrator
    except Exception as e:
        result["ok"] = False
        result["error"] = f"plan build exception: {e}"

    try:
        pipeline_config = _detect_pipeline_config_from_disk(change)
        result["pipeline_config"] = pipeline_config
    except Exception as e:
        result["error"] = f"detect config failed: {e}"

    return result


def cli_env_action(change: str, phase_name: str, stage_name: str, env_name: str,
                   hook_timeout_seconds: int | None = None) -> dict[str, Any]:
    """CLI 入口：解析 env hook 执行 plan（不执行），写 *_STARTED 事件。

    v2.1.1 重构：不再执行 env hook。编排器收到 plan 后自己 bash 执行。
    """
    result: dict[str, Any] = {
        "action": "env_action_plan",
        "ok": False,
        "phase": phase_name,
        "stage": stage_name,
        "env_name": env_name,
        "plan": None,
        "skipped": False,
        "error": None,
        "started_event_ts": None,
    }

    change_root = os.path.join(CHANGES_DIR, change)
    event_log = EventLog(change_root=change_root)
    started_type = (
        EVT_PREPARE_ENV_STARTED if phase_name == "prepare_env"
        else EVT_CLEAN_ENV_STARTED
    )
    try:
        ev = event_log.append(started_type, {
            "stage": stage_name,
            "env_name": env_name,
        })
        result["started_event_ts"] = ev.get("ts")
    except Exception as e:
        result.setdefault("warnings", []).append(f"event_log start append failed: {e}")

    try:
        plan = _build_env_hook_plan(
            change, phase_name,
            explicit_env_name=env_name,
            explicit_stage_name=stage_name,
            explicit_timeout=hook_timeout_seconds,
        )
    except Exception as e:
        result["error"] = f"plan build exception: {e}"
        return result

    if not plan.get("ok"):
        result["error"] = plan.get("error", "plan build failed")
        return result

    if plan.get("skipped"):
        result["ok"] = True
        result["skipped"] = True
        return result

    _inline_env_into_command(plan)
    plan_for_orchestrator = {k: v for k, v in plan.items() if k != "env"}
    result["plan"] = plan_for_orchestrator
    result["ok"] = True
    return result


def cli_env_action_result(
    change: str,
    phase_name: str,
    stage_name: str,
    env_name: str,
    success: bool,
    log_path: str = "",
    exit_code: int | None = None,
    started_event_ts: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """CLI 入口：env hook 执行完毕，编排器汇报结果。

    v2.1.1 新增：编排器在 bash 执行完 env hook 后调用本命令：
      - 写 *_COMPLETED event（event schema 中字段名为 ok: bool，保留历史兼容）
      - 更新 state (stage_prepared / current_stage)
      - 写 pipeline.snapshot.json

    v2.x 变更：参数 ok → success（与 CLI 入参名对齐）。
    """
    from pipeline.snapshot import load_snapshot, save_snapshot
    from pipeline.state import PipelineState

    result: dict[str, Any] = {
        "action": "env_action_result",
        "ok": False,
        "phase": phase_name,
        "stage": stage_name,
        "env_name": env_name,
        "stage_prepared": [],
        "current_stage": "",
        "error": None,
    }

    change_root = os.path.join(CHANGES_DIR, change)
    event_log = EventLog(change_root=change_root)

    completed_type = (
        EVT_PREPARE_ENV_COMPLETED if phase_name == "prepare_env"
        else EVT_CLEAN_ENV_COMPLETED
    )
    completed_data: dict[str, Any] = {
        "stage": stage_name,
        "env_name": env_name,
        "exit_code": exit_code,
        "log_path": log_path,
        "ok": success,  # event schema 字段名保留 "ok"（pipeline.events 历史兼容）
    }
    if started_event_ts:
        completed_data["started_ts"] = started_event_ts
    try:
        event_log.append(completed_type, completed_data)
    except Exception as e:
        result.setdefault("warnings", []).append(f"event_log complete append failed: {e}")

    if not success:
        result["ok"] = False
        result["error"] = error or f"{phase_name} failed (exit_code={exit_code})"
        return result

    state = load_snapshot(change_root) or PipelineState(change=change)
    new_prepared = set(state.stage_prepared)
    new_current = state.current_stage

    if phase_name == "prepare_env":
        if stage_name:
            new_prepared.add(stage_name)
        new_current = stage_name
    elif phase_name == "clean_env":
        if stage_name:
            new_prepared.discard(stage_name)

    new_state = state.replace(
        stage_prepared=new_prepared,
        current_stage=new_current,
    )
    try:
        save_snapshot(change_root, new_state)
    except Exception as e:
        result["error"] = f"save_snapshot failed: {e}"
        return result

    result["ok"] = True
    result["stage_prepared"] = sorted(new_prepared)
    result["current_stage"] = new_current
    return result
