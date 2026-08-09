#!/usr/bin/env python3
"""pg-invoke-hook.py — runtime 层统一 LLM-facing 入口 (env hooks + role actions).

抽取自 pg-pipeline-runner.py:cmd_invoke_hook (v3.1) 主体实现, 提升到 runtime 层后
供 pg-build / pg-fix-issue / pg-regression 三个 SKILL + pg-run 手动调用 + agent
ad-hoc 调用共享. pg-pipeline-runner.py 保留同名子命令 (thin wrapper) 以保证向后兼容.

设计动机:
- pg-pipeline-runner.py 同时承担 "编排状态机" (next/record/check) 与
  "hook executor" (invoke-hook) 两类职责. 抽离后:
  * executor 归 runtime/bin/ (CLAUDE.md 仓库结构第 28-30 行预留位置)
  * SKILL 之间不再互相依赖 runner 路径
  * 测试可走 subprocess.run 黑盒, 不需 mock sys.argv

v5 协议 (current):
- --change → --session (canonical). --change 保留 1 版本作为 deprecated alias.
- --skill / --caller 硬缺省 'ad-hoc', 任何漏传 caller 的调用都落到 .pg/ad-hoc/.
- 新增 --log-dir (调试覆盖), --timeout-override (ad-hoc 调试, 输出 WARN).
- caller 维度路由:
    pg-build       -> .pg/changes/<session>/2-build/<env>-logs
    pg-regression  -> .pg/regression/<session>/<env>-logs
    pg-fix-issue   -> .pg/fix-issue/<session>/<env>-logs
    pg-quick-build -> .pg/quick-build/<session>/<env>-logs
    ad-hoc         -> .pg/ad-hoc/<session>/<env>-logs

顶级 subcommands:
- invoke-hook — 触发 role action (start/stop/restart/logs/tail/health_check) 或 env-level hook
  (prepare_env/clean_env). 内部反查 project.yaml, 渲染 spec, 调 pg-run-hook.py.
- status     — 透传 prepare_env 状态查询到 pg-pipeline-runner.py
  prepare-env-status 子命令 (stdout JSON 透传, exit code 透传).
  LLM-facing 入口统一在 runtime 层, 与 invoke-hook 平级.

支持的动作 (仅 invoke-hook):
- per-role (需 --role + --instance):
  * start / stop / restart / logs / tail / health_check
- env-level (忽略 --role/--instance):
  * prepare_env / clean_env

Usage:
  python3 pg-invoke-hook.py invoke-hook \\
    --session <S> --env <ENV> --role <ROLE> --instance <I> --action <A> \\
    [--stage <ST>] [--tail-lines <N>] [--skill pg-build|pg-regression|pg-fix-issue|pg-quick-build|ad-hoc] \\
    [--log-dir <DIR>] [--timeout-override <SECS>]

  python3 pg-invoke-hook.py invoke-hook \\
    --session <S> --env <ENV> --action prepare_env \\
    [--skill pg-build|pg-regression|pg-fix-issue|pg-quick-build|ad-hoc]

  python3 pg-invoke-hook.py status --change <C> [--stage <ST>]

Args:
  --session         session 名 (canonical). 与 caller 正交. 留空 + caller=ad-hoc →
                     自动生成 auto-<date>-<pid>.
  --change          DEPRECATED alias of --session (1 版本兼容).
  --env             environment name (必须在 project.yaml environments 中)
  --stage           stage name (默认: manual)
  --role            role name (backend/frontend/agent); per-role 必填, env-level 忽略
  --instance        instance name; per-role 必填, env-level 忽略
  --action          start|stop|logs|tail (per-role) 或 prepare_env|clean_env (env-level)
  --tail-lines      (logs/tail only) 追加 --tail-lines N 到 hook args 末尾
  --skill / --caller 调用方身份. 硬缺省 'ad-hoc'. SKILL 调用必须显式标注.
  --log-dir         显式覆盖日志目录 (优先级最高, 用于 agent 调试).
  --timeout-override 覆盖 project.yaml timeout_seconds (ad-hoc 调试, 输出 WARN).

Spec 渲染 (v5):
  cmd             = "bash " + shlex.quote(act_cfg["script"]) + (args if any)
  env vars 注入    = PG_RUN_SESSION / PG_RUN_CALLER / PG_STAGE / PG_ENV /
                    PG_ROLE / PG_INSTANCE_NAME / PG_INSTANCE_HOST / PG_HOOK_TYPE /
                    PG_HOOK_LOG_DIR / PG_LOG_FILE / PG_RESULT_FILE
                    (完整 SSOT 见 src/runtime/spec/hook-env-vars.yaml)
  timeout_seconds  = act_cfg["timeout_seconds"] (可被 --timeout-override 覆盖)
  log_path        = per-caller 路由 (see pg_log_dir_for_skill):
                    pg-build       -> .pg/changes/<session>/2-build/<env>-logs
                    pg-regression  -> .pg/regression/<session>/<env>-logs
                    pg-fix-issue   -> .pg/fix-issue/<session>/<env>-logs
                    ad-hoc         -> .pg/ad-hoc/<session>/<env>-logs
  wait_for_completion (bool, 默认 start=False / 其他=True):
                    start action 默认 fire-and-forget, hook 用 pg_start_bg
                    setsid detach 服务后立即返回, 避免 pg-run-hook.py 的 timeout
                    误杀 detached 后台服务. stop/logs/tail 强制 True, 必须等
                    hook 跑完. CLI 可用 --wait-for-completion / --no-wait-for-bg
                    显式覆盖.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path


# ----- 路径解析 -----

def find_project_root() -> Path:
    """从 .pg/project.yaml 找项目根.

    优先级:
      1. PG_PROJECT_ROOT 环境变量
      2. 当前 cwd
      3. 本脚本向上 6 层内查找
    """
    env_root = os.environ.get("PG_PROJECT_ROOT")
    if env_root and _has_config(Path(env_root)):
        return Path(env_root)

    cwd = Path.cwd()
    if _has_config(cwd):
        return cwd

    p = Path(__file__).resolve().parent
    for _ in range(6):
        if _has_config(p):
            return p
        p = p.parent
    return cwd


def _has_config(path: Path) -> bool:
    return (
        (path / ".pg" / "project.yaml").is_file()
        or (path / "pg-spec" / "config.yaml").is_file()
    )


def find_pg_skills_root(project_root: Path) -> Path:
    """反推 pg-skills subtree 根 (.pg/skills/)."""
    return project_root / ".pg" / "skills"


# 与原 cmd_invoke_hook 一致 (line 3166-3168)
ENV_LEVEL_ACTIONS = ("prepare_env", "describe_env", "clean_env", "restart_all_instances")

# Caller 维度枚举 (与 .pg/hooks/lib/common.sh:pg_resolve_paths 的 case 分支同步)
CALLER_PG_BUILD = "pg-build"
CALLER_PG_REGRESSION = "pg-regression"
CALLER_PG_FIX_ISSUE = "pg-fix-issue"
CALLER_PG_PROPOSE = "pg-propose"
CALLER_PG_QUICK_BUILD = "pg-quick-build"
CALLER_PG_AGENT = "pg-agent"
CALLER_AD_HOC = "ad-hoc"
KNOWN_CALLERS = (CALLER_PG_BUILD, CALLER_PG_REGRESSION, CALLER_PG_FIX_ISSUE, CALLER_PG_PROPOSE, CALLER_PG_QUICK_BUILD, CALLER_PG_AGENT, CALLER_AD_HOC)

# v6 新增: describe_env 触发者 (生成 env-description.yaml 供下游消费)
# v7: caller=ad-hoc 也允许 (pg-run 手动探测, 落到 .pg/ad-hoc/<session>/)
# v2.1: caller=pg-quick-build 也允许 (落到 .pg/quick-build/<session>/, 不污染 .pg/changes/)
DESCRIBE_ENV_CALLERS = (CALLER_PG_PROPOSE, CALLER_PG_FIX_ISSUE, CALLER_PG_REGRESSION, CALLER_PG_QUICK_BUILD, CALLER_AD_HOC)


def _resolve_wait_for_completion(action: str, cli_value, cfg_value=None):
    """Decide wait_for_completion for the spec.

    优先级 (高→低):
      1. cli_value is not None → 用户命令行显式指定 (最高优先)
      2. cfg_value is not None → YAML 中 action 级的 wait_for_completion 字段
      3. 默认规则:
           start  → True (等待服务就绪, 需 fire-and-forget 的调用方显式传 --no-wait-for-bg)
           其他   → True (stop/logs/tail 必须等 hook 跑完)

    Note: stop/logs/tail 即使用户传 --no-wait-for-bg 也按 True 处理 — 这些
    action 必须等 hook 跑完才有意义 (eg. stop 后 hook 才能写 result.json 报告
    成功/失败).
    """
    if action != "start":
        return True
    if cli_value is not None:
        return bool(cli_value)
    if cfg_value is not None:
        return bool(cfg_value)
    return True


def resolve_session(session: str, caller: str) -> str:
    """session 名解析 (v4 协议).

    - session 留空 + caller=ad-hoc → 自动生成 auto-<date>-<pid>
    - session 留空 + caller 是 SKILL caller → 报错 (SKILL 必须显式传)
    - session 非空 → 原样返回
    """
    if session:
        return session
    if caller == CALLER_AD_HOC:
        return f"auto-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    sys.stderr.write(
        f"Error: --caller={caller} requires explicit --session (got empty)\n"
    )
    sys.exit(2)


def pg_log_dir_for_skill(caller: str, session: str, env: str, project_root: Path) -> Path:
    """Return the per-caller log directory for hook logs (v4 协议).

    Routing rules (must stay in sync with .pg/hooks/lib/common.sh:pg_resolve_paths):
      pg-build       -> .pg/changes/<session>/2-build/<env>-logs
      pg-regression  -> .pg/regression/<session>/<env>-logs   (session = <suite>-<date>-<seq>)
      pg-fix-issue   -> .pg/fix-issue/<session>/<env>-logs    (session 已含 fix- 前缀)
      pg-propose     -> .pg/changes/<change-id>/2-propose/<env>-logs
      pg-quick-build -> .pg/quick-build/<session>/<env>-logs  (独立命名空间, 不与 .pg/changes/ 混)
      pg-agent       -> .pg/agent/<session>/<env>-logs        (LLM agent 通用入口, session = <iso-date>-<keyword>)
      ad-hoc         -> .pg/ad-hoc/<session>/<env>-logs       (独立顶级目录, 不与 SKILL 命名空间混)
    """
    base = project_root / ".pg"
    dir_name = f"{env}-logs"
    if caller == CALLER_PG_BUILD:
        return base / "changes" / session / "2-build" / dir_name
    if caller == CALLER_PG_REGRESSION:
        return base / "regression" / session / dir_name
    if caller == CALLER_PG_FIX_ISSUE:
        return base / "fix-issue" / session / dir_name
    if caller == CALLER_PG_PROPOSE:
        # v1.1: 与产物目录对齐 — 剥离 session 的 ISO 日期前缀 (<date>-<change-id> →
        # <change-id>), 日志落在 .pg/changes/<change-id>/2-propose/, 避免产物与
        # 日志分裂到两个目录. 日志文件名含日期时间戳, 不会与归档日期目录冲突.
        _m = re.match(r"^\d{4}-\d{2}-\d{2}-(.+)$", session)
        _change = _m.group(1) if _m else session
        return base / "changes" / _change / "2-propose" / dir_name
    if caller == CALLER_PG_QUICK_BUILD:
        return base / "quick-build" / session / dir_name
    if caller == CALLER_PG_AGENT:
        return base / "agent" / session / dir_name
    # ad-hoc
    return base / "ad-hoc" / session / dir_name


def build_env_level_hook_spec(
    session: str,
    env: str,
    stage: str,
    action: str,
    act_cfg: dict,
    project_root: Path,
    caller: str = CALLER_AD_HOC,
) -> dict:
    """Build pg-run-hook.py spec for environment-level hooks (prepare_env / clean_env).

    Environment-level hooks live directly under environments.<env>.<action>
    (NOT under roles.<role>.actions). They have no role/instance. We render
    a spec shape that pg-run-hook.py can consume: role/instance_host are
    empty strings; log_path is namespaced under env-level hooks subdir so
    it doesn't collide with role.* action logs.

    caller: 调用方身份 (pg-build / pg-regression / pg-fix-issue / pg-quick-build / ad-hoc).
            注入为 PG_RUN_CALLER via pg-run-hook.py.
    """
    rendered_args = []
    for raw in (act_cfg.get("args") or []):
        rendered_args.append(str(raw))

    inner_cmd = "bash " + shlex.quote(act_cfg["script"])
    if rendered_args:
        inner_cmd += " " + " ".join(shlex.quote(a) for a in rendered_args)

    hook_log_dir = pg_log_dir_for_skill(caller, session, env, project_root)
    log_path = str(hook_log_dir / f"env.{action}.log")
    result_path = str(hook_log_dir / f"env.{action}.result.json")

    spec = {
        "cmd": inner_cmd,
        "session": session,
        "change": session,
        "stage": stage,
        "env": env,
        "role": "",
        "instance_name": "",
        "instance_host": "",
        "hook_type": action,
        "timeout_seconds": act_cfg.get("timeout_seconds"),
        "log_path": log_path,
        "hook_log_dir": str(hook_log_dir),
        "hook_result_path": result_path,
        "caller": caller,
        "wait_for_completion": True,
    }
    return spec


def build_describe_env_spec(
    session: str,
    env: str,
    stage: str,
    act_cfg: dict,
    project_root: Path,
    caller: str,
) -> dict:
    """Build pg-run-hook.py spec for describe_env (v6 新增, v7 caller 扩 ad-hoc).

    describe_env 与 prepare_env / clean_env 同属 env-level, 但有两个差异:
      1. 必须注入 PG_CHANGE_ID + PG_OUTPUT_PATH (脚本写入 env-description.yaml)
      2. caller 限定为 pg-propose / pg-fix-issue / pg-regression / pg-quick-build / ad-hoc
         (其他 caller 调用直接报错)

    语义契约: describe_env 的产出描述的是 prepare_env 成功执行后的预期基线.
    pg-build 在 bootstrap 阶段先调 prepare_env 确保成功, 再 dispatch scenario
    track, 届时环境状态应与 env-description.yaml 一致.

    输出路径按 caller 路由 (统一用 --session 作为路径派生源, 不再单独传 --change-id):
      pg-propose     -> .pg/changes/<session>/env-description.yaml
      pg-fix-issue   -> .pg/fix-issue/<session>/env-description.yaml
      pg-regression  -> .pg/regression/<session>/env-description.yaml
      pg-quick-build -> .pg/quick-build/<session>/env-description.yaml
      ad-hoc         -> .pg/ad-hoc/<session>/env-description.yaml

    脚本超时默认 60s (仅探测, 不应长跑); YAML 缺 timeout_seconds 时回落到此值.
    """
    inner_cmd = "bash " + shlex.quote(act_cfg["script"])

    hook_log_dir = pg_log_dir_for_skill(caller, session, env, project_root)
    log_path = str(hook_log_dir / "env.describe_env.log")
    result_path = str(hook_log_dir / "env.describe_env.result.json")

    # 从 session 中剥离 ISO 日期前缀 (YYYY-MM-DD-) 得到 change_id,
    # 用于产物路径 (正常目录名, 不含日期前缀).
    # 若 session 无日期前缀则保持原样.
    _match = re.match(r"^\d{4}-\d{2}-\d{2}-(.+)$", session)
    change_id = _match.group(1) if _match else session

    if caller == CALLER_PG_PROPOSE:
        output_path = str(project_root / ".pg" / "changes" / change_id / "env-description.yaml")
    elif caller == CALLER_PG_FIX_ISSUE:
        output_path = str(project_root / ".pg" / "fix-issue" / change_id / "env-description.yaml")
    elif caller == CALLER_PG_REGRESSION:
        output_path = str(project_root / ".pg" / "regression" / change_id / "env-description.yaml")
    elif caller == CALLER_PG_QUICK_BUILD:
        output_path = str(project_root / ".pg" / "quick-build" / change_id / "env-description.yaml")
    elif caller == CALLER_AD_HOC:
        output_path = str(project_root / ".pg" / "ad-hoc" / change_id / "env-description.yaml")
    else:
        sys.stderr.write(
            f"Error: --action describe_env requires caller in {DESCRIBE_ENV_CALLERS}, got '{caller}'\n"
        )
        sys.exit(2)

    spec = {
        "cmd": inner_cmd,
        "session": session,
        "change": session,
        "stage": stage,
        "env": env,
        "role": "",
        "instance_name": "",
        "instance_host": "",
        "hook_type": "describe_env",
        "timeout_seconds": act_cfg.get("timeout_seconds") or 60,
        "log_path": log_path,
        "hook_log_dir": str(hook_log_dir),
        "hook_result_path": result_path,
        "caller": caller,
        "change_id": change_id,
        "output_path": output_path,
        "wait_for_completion": True,
    }
    return spec


def build_restart_all_specs(
    session: str,
    env: str,
    stage: str,
    env_cfg: dict,
    project_root: Path,
    caller: str = CALLER_AD_HOC,
) -> list:
    """构建 restart_all_instances 的三阶段 spec list.

    行为:
      Phase 1 — 逆序停止所有 instance (reversed(roles) × reversed(instances)).
      Phase 2 — 正序启动所有 instance (YAML 顺序, 沿用 per-role start wait_for_completion).
      Phase 3 — 正序 health_check (仅当 role.actions.health_check 已声明).

    返回 list[dict], 每个元素是 build_role_hook_spec 生成的 spec. 调用方负责
    按顺序 fork-exec pg-run-hook.py 并聚合整体退出码.
    """
    roles = env_cfg.get("roles") or []
    specs: list = []

    # Phase 1: 逆序停止所有 instance
    for role in reversed(roles):
        role_name = role.get("name", "")
        role_cfg = role
        stop_cfg = (role_cfg.get("actions") or {}).get("stop")
        if not stop_cfg:
            continue
        for inst in reversed(role_cfg.get("instances") or []):
            inst_obj = inst if isinstance(inst, dict) else {"name": inst}
            specs.append(build_role_hook_spec(
                session=session, env=env, stage=stage, action="stop",
                role=role_name, instance=inst_obj["name"],
                instance_host=inst_obj.get("host", ""),
                act_cfg=stop_cfg, tail_lines=None,
                project_root=project_root, caller=caller,
                wait_for_completion=True,
            ))

    # Phase 2: 正序启动所有 instance
    for role in roles:
        role_name = role.get("name", "")
        role_cfg = role
        start_cfg = (role_cfg.get("actions") or {}).get("start")
        if not start_cfg:
            continue
        for inst in role_cfg.get("instances") or []:
            inst_obj = inst if isinstance(inst, dict) else {"name": inst}
            specs.append(build_role_hook_spec(
                session=session, env=env, stage=stage, action="start",
                role=role_name, instance=inst_obj["name"],
                instance_host=inst_obj.get("host", ""),
                act_cfg=start_cfg, tail_lines=None,
                project_root=project_root, caller=caller,
                wait_for_completion=_resolve_wait_for_completion(
                    "start", None, start_cfg.get("wait_for_completion")
                ),
            ))

    # Phase 3: 正序 health_check (仅当 role.actions.health_check 已声明)
    for role in roles:
        role_name = role.get("name", "")
        role_cfg = role
        hc_cfg = (role_cfg.get("actions") or {}).get("health_check")
        if not hc_cfg:
            continue
        for inst in role_cfg.get("instances") or []:
            inst_obj = inst if isinstance(inst, dict) else {"name": inst}
            specs.append(build_role_hook_spec(
                session=session, env=env, stage=stage, action="health_check",
                role=role_name, instance=inst_obj["name"],
                instance_host=inst_obj.get("host", ""),
                act_cfg=hc_cfg, tail_lines=None,
                project_root=project_root, caller=caller,
                wait_for_completion=True,
            ))

    return specs


def _find_role(env_cfg: dict, role_name: str) -> dict | None:
    """在 environments.<env>.roles 数组中按 name 字段查找 role_cfg.

    project.yaml 的 roles 是 array of {name, ...}, 不是 dict (v3.7+ 设计).
    源码顺序保留, 但查找必须显式遍历. 未找到返回 None.
    """
    for role in (env_cfg.get("roles") or []):
        if isinstance(role, dict) and role.get("name") == role_name:
            return role
    return None


def build_role_hook_spec(
    session: str,
    env: str,
    stage: str,
    action: str,
    role: str,
    instance: str,
    instance_host: str,
    act_cfg: dict,
    tail_lines,
    project_root: Path,
    caller: str = CALLER_AD_HOC,
    wait_for_completion: bool = True,
) -> dict:
    """Build pg-run-hook.py spec for per-role actions (start/stop/restart/logs/tail/health_check).

    wait_for_completion:
        True  (默认) — 等 hook 进程跑完, 超时则 proc.kill(). 适合 stop/logs/tail.
        False — fire-and-forget. 适合 start action: hook 用 pg_start_bg 把
                服务 detach 到新 session 后立即返回, 后台服务不受 pg-run-hook.py
                timeout 影响. 调用方 (invoke-hook CLI) 对 start action 默认开.
    """
    rendered_args = []
    for raw in (act_cfg.get("args") or []):
        a = str(raw)
        a = a.replace("{role}", role)
        a = a.replace("{instance.name}", instance)
        a = a.replace("{instance.host}", instance_host)
        # Resolve {lines:N} template — use --tail-lines CLI value if given, else extract N
        if a.startswith("{lines:") and a.endswith("}"):
            default_lines = a[7:-1]
            a = str(tail_lines if tail_lines is not None else default_lines)
        rendered_args.append(a)

    # Option Y: --tail-lines is appended to hook args list (logs/tail only).
    # Only append if we didn't already consume it above (i.e., there was no {lines:N}
    # template in the YAML args).
    has_lines_template = any(
        str(raw).startswith("{lines:") for raw in (act_cfg.get("args") or [])
    )
    if action in ("logs", "tail") and tail_lines is not None and not has_lines_template:
        rendered_args.extend(["--tail-lines", str(tail_lines)])

    inner_cmd = "bash " + shlex.quote(act_cfg["script"])
    if rendered_args:
        inner_cmd += " " + " ".join(shlex.quote(a) for a in rendered_args)

    hook_log_dir = pg_log_dir_for_skill(caller, session, env, project_root)
    log_path = str(hook_log_dir / f"role.{role}.{action}@{instance}.log")
    result_path = str(hook_log_dir / f"role.{role}.{action}@{instance}.result.json")

    spec = {
        "cmd": inner_cmd,
        "session": session,
        "change": session,
        "stage": stage,
        "env": env,
        "role": role,
        "instance_name": instance,
        "instance_host": instance_host,
        "hook_type": action,
        "timeout_seconds": act_cfg.get("timeout_seconds"),
        "log_path": log_path,
        "hook_log_dir": str(hook_log_dir),
        "hook_result_path": result_path,
        "caller": caller,
        "wait_for_completion": wait_for_completion,
    }
    return spec


# ----- 主流程 -----

def _load_yaml():
    """Lazy import yaml (project may not have it pre-installed; pg-skills assumes it)."""
    try:
        import yaml
    except ImportError:
        sys.stderr.write(
            "Error: PyYAML is required. Install via `pip install pyyaml`.\n"
        )
        sys.exit(2)
    return yaml


def invoke_hook_main(argv=None) -> int:
    """LLM-facing entry point for triggering role actions (start/stop/restart/logs/tail/health_check)
    and environment-level hooks (prepare_env/clean_env).

    Resolves the action in project.yaml, renders args (with
    {role}/{instance.name}/{instance.host} placeholders), appends
    --tail-lines <N> if action is logs|tail and the flag was given,
    builds the pg-run-hook.py spec, and spawns the hook executor.

    timeout_seconds is read from project.yaml by default and passed through
    to pg-run-hook.py. CLI can override via --timeout-override (ad-hoc only,
    outputs WARN).

    --session (canonical) replaces --change (deprecated alias, 1 version compat).
    --skill / --caller defaults to 'ad-hoc' (hard default, not empty string).
    """
    parser = argparse.ArgumentParser(
        prog="pg-invoke-hook.py invoke-hook",
        description=(
            "Trigger a role action (start/stop/restart/logs/tail/health_check) or env-level hook "
            "(prepare_env/clean_env) via pg-run-hook.py. Used by SKILL "
            "orchestrators (pg-build / pg-fix-issue / pg-regression / pg-quick-build) and by "
            "agent ad-hoc / pg-run manual calls. NOT part of any pipeline state "
            "machine."
        ),
    )
    parser.add_argument("--session", default="",
                        help=(
                            "session 名 (与 caller 正交). "
                            "pg-build: 提案名; pg-regression: <suite>-<date>-<seq>; "
                            "pg-fix-issue: fix-<date>-<slug>; "
                            "pg-quick-build: <iso-date>-<keyword>; "
                            "ad-hoc 留空: 自动生成 auto-<date>-<pid>."
                        ))
    parser.add_argument("--change", default=None,
                        help=(
                            "DEPRECATED alias of --session. 仅作 1 个版本兼容, "
                            "SKILL / pg-run / agent 应改为 --session."
                        ))
    parser.add_argument("--env", required=True,
                        help="environment name (must be in project.yaml environments)")
    parser.add_argument("--stage", default="manual",
                        help="stage name (default: manual)")
    parser.add_argument("--role", required=False,
                        help=(
                            "role name (backend/frontend/agent). Required "
                            "for per-role actions (start/stop/restart/logs/tail/health_check); "
                            "ignored for environment-level actions "
                            "(prepare_env/clean_env)."
                        ))
    parser.add_argument("--instance", required=False,
                        help=(
                            "instance name. Must exist in "
                            "environments.<env>.roles.<role>.instances. "
                            "Required for per-role actions; ignored for "
                            "environment-level actions."
                        ))
    parser.add_argument("--action", required=True,
                        choices=["start", "stop", "restart", "logs", "tail",
                                 "health_check",
                                 "prepare_env", "describe_env", "clean_env",
                                 "restart_all_instances"],
                        help=(
                            "action to trigger. start/stop/restart/logs/tail/health_check are "
                            "per-role lifecycle actions (require --role and "
                            "--instance); prepare_env/describe_env/clean_env/restart_all_instances "
                            "are environment-level lifecycle hooks (ignore --role/--instance). "
                            "describe_env (v6): caller 限定 pg-propose/pg-fix-issue/pg-regression/pg-quick-build, "
                            "自动注入 PG_CHANGE_ID + PG_OUTPUT_PATH, 写入 env-description.yaml."
                        ))
    parser.add_argument("--tail-lines", type=int, default=None,
                        help="(logs/tail only) append --tail-lines N to hook args")
    parser.add_argument("--skill", "--caller", dest="caller", default=CALLER_AD_HOC,
                        choices=list(KNOWN_CALLERS),
                        help=(
                            "调用方身份 (caller 维度路由). "
                            "硬缺省 'ad-hoc' — 任何不显式传 --skill 的调用都视为 ad-hoc, "
                            "日志落到 .pg/ad-hoc/<session>/<env>-logs/."
                            "SKILL (pg-build / pg-regression / pg-fix-issue / pg-propose / pg-quick-build) 必须显式标注."
                        ))
    parser.add_argument("--log-dir", default=None,
                        help=(
                            "显式覆盖日志目录. 优先级最高 (覆盖 caller/session/env 推导), "
                            "用于 agent ad-hoc 调试. 透传 PG_HOOK_LOG_DIR 到 hook."
                        ))
    parser.add_argument("--timeout-override", type=int, default=None,
                        help=(
                            "覆盖 project.yaml 的 timeout_seconds (ad-hoc 调试用). "
                            "CLI 显式传时会输出 WARN 提示覆盖值."
                        ))
    parser.add_argument("--no-wait-for-bg", dest="wait_for_completion",
                        action="store_false", default=None,
                        help=(
                            "fire-and-forget 模式: hook 用 pg_start_bg setsid detach "
                            "启动服务后立即返回, 不等子进程完成. start action 默认开, "
                            "stop/logs/tail 显式 --no-wait-for-bg 无效 (会被忽略, "
                            "这些 action 必须等 hook 跑完)."
                        ))
    parser.add_argument("--wait-for-completion", dest="wait_for_completion",
                        action="store_true", default=None,
                        help=(
                            "强制等待 hook 跑完 (覆盖 start action 的 fire-and-forget 默认). "
                            "调试时偶尔有用: 想看 hook 自己 exit 前的输出或耗时."
                        ))

    # argv layout: caller passes [program_name, "invoke-hook", *flags].
    # For test convenience we accept both [program, "invoke-hook", ...]
    # and [program, ...] (auto-prepend "invoke-hook" subcommand).
    if argv is None:
        argv = sys.argv
    if len(argv) < 2 or argv[1] != "invoke-hook":
        argv = [argv[0], "invoke-hook", *argv[1:]]
    args = parser.parse_args(argv[1:][1:])  # slice off program name AND "invoke-hook"

    # --change deprecated alias: 合并到 session
    if not args.session and args.change:
        sys.stderr.write(
            "WARN: --change is deprecated, use --session instead\n"
        )
        args.session = args.change

    # Per-role actions require --role and --instance at the CLI level.
    if args.action not in ENV_LEVEL_ACTIONS:
        if not args.role:
            sys.stderr.write(
                f"Error: --action {args.action} requires --role\n"
            )
            return 1
        if not args.instance:
            sys.stderr.write(
                f"Error: --action {args.action} requires --instance\n"
            )
            return 1

    # session 解析 (留空 + caller=ad-hoc → 自动生成)
    args.session = resolve_session(args.session, args.caller)

    project_root = find_project_root()
    yaml = _load_yaml()
    config_path = project_root / ".pg" / "project.yaml"
    if not config_path.is_file():
        sys.stderr.write(
            f"Error: project.yaml not found at {config_path}\n"
        )
        return 2

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    env_cfg = (config.get("environments") or {}).get(args.env) or {}

    # Environment-level lifecycle hooks (prepare_env / describe_env / clean_env /
    # restart_all_instances) are NOT role-scoped: prepare_env / describe_env /
    # clean_env live directly under environments.<env>.
    # restart_all_instances 走特殊路径: 内部展开为 stop → start → health_check 三阶段.
    if args.action in ENV_LEVEL_ACTIONS:
        if args.action == "restart_all_instances":
            # restart_all_instances 忽略 --role/--instance (env-level action)
            if args.role or args.instance:
                sys.stderr.write(
                    "Error: --action restart_all_instances ignores --role/--instance\n"
                )
                return 1
            if not env_cfg.get("roles"):
                sys.stderr.write(
                    f"Error: env '{args.env}' has no roles defined\n"
                )
                return 1
            specs = build_restart_all_specs(
                session=args.session,
                env=args.env,
                stage=args.stage,
                env_cfg=env_cfg,
                project_root=project_root,
                caller=args.caller,
            )
            if not specs:
                sys.stderr.write(
                    f"Error: env '{args.env}' has no startable/stoppable instances "
                    f"(no role.actions.start or stop defined)\n"
                )
                return 1
            # 走多 spec 流程 (后续 _run_multi_specs 处理)
            spec = {"_multi_specs": specs, "action": "restart_all_instances"}
        elif args.action == "describe_env":
            # describe_env: 必须显式声明在 environments.<env>.describe_env, 且 caller 必须是
            # pg-propose / pg-fix-issue / pg-regression (其他 caller 无语义).
            if args.caller not in DESCRIBE_ENV_CALLERS:
                sys.stderr.write(
                    f"Error: --action describe_env requires --caller in "
                    f"{DESCRIBE_ENV_CALLERS}, got '{args.caller}'\n"
                )
                return 1
            if not args.session:
                sys.stderr.write(
                    "Error: --action describe_env requires --session\n"
                )
                return 1
            describe_cfg = env_cfg.get("describe_env")
            if not describe_cfg:
                sys.stderr.write(
                    f"Error: action 'describe_env' not defined in "
                    f"environments.{args.env}.describe_env (must be explicit)\n"
                )
                return 1
            script_path = project_root / describe_cfg["script"]
            if not script_path.is_file():
                sys.stderr.write(
                    f"Error: describe_env script not found: {script_path}\n"
                )
                return 2
            spec = build_describe_env_spec(
                session=args.session,
                env=args.env,
                stage=args.stage,
                act_cfg=describe_cfg,
                project_root=project_root,
                caller=args.caller,
            )
        else:
            env_hook_cfg = env_cfg.get(args.action)
            if not env_hook_cfg:
                sys.stderr.write(
                    f"Error: action '{args.action}' not defined in "
                    f"environments.{args.env}\n"
                )
                return 1
            spec = build_env_level_hook_spec(
                session=args.session,
                env=args.env,
                stage=args.stage,
                action=args.action,
                act_cfg=env_hook_cfg,
                project_root=project_root,
                caller=args.caller,
            )
    else:
        # Per-role lifecycle action (start / stop / restart / logs / tail / health_check).
        role_cfg = _find_role(env_cfg, args.role)
        if role_cfg is None:
            sys.stderr.write(
                f"Error: role '{args.role}' not defined in environments.{args.env}.roles\n"
            )
            return 1
        if args.action not in (role_cfg.get("actions") or {}):
            if args.action == "restart":
                # Fallback: stop → start → [health_check] when restart hook not defined.
                stop_cfg = (role_cfg.get("actions") or {}).get("stop")
                start_cfg = (role_cfg.get("actions") or {}).get("start")
                if not stop_cfg:
                    sys.stderr.write(
                        f"Error: action 'restart' not defined in role '{args.role}', "
                        f"and fallback 'stop' is also not defined\n"
                    )
                    return 1
                if not start_cfg:
                    sys.stderr.write(
                        f"Error: action 'restart' not defined in role '{args.role}', "
                        f"and fallback 'start' is also not defined\n"
                    )
                    return 1

                instance_obj = next(
                    (i for i in (role_cfg.get("instances") or [])
                     if i.get("name") == args.instance),
                    None,
                )
                if not instance_obj:
                    sys.stderr.write(
                        f"Error: instance '{args.instance}' not found in "
                        f"environments.{args.env}.roles.<role>.instances\n"
                    )
                    return 1
                instance_host = instance_obj.get("host", "")

                stop_spec = build_role_hook_spec(
                    session=args.session, env=args.env, stage=args.stage,
                    action="stop", role=args.role,
                    instance=args.instance, instance_host=instance_host,
                    act_cfg=stop_cfg, tail_lines=args.tail_lines,
                    project_root=project_root, caller=args.caller,
                    wait_for_completion=True,
                )
                start_spec = build_role_hook_spec(
                    session=args.session, env=args.env, stage=args.stage,
                    action="start", role=args.role,
                    instance=args.instance, instance_host=instance_host,
                    act_cfg=start_cfg, tail_lines=args.tail_lines,
                    project_root=project_root, caller=args.caller,
                    wait_for_completion=_resolve_wait_for_completion(
                        "start", args.wait_for_completion, start_cfg.get("wait_for_completion")
                    ),
                )
                specs = [stop_spec, start_spec]

                hc_cfg = (role_cfg.get("actions") or {}).get("health_check")
                if hc_cfg:
                    hc_spec = build_role_hook_spec(
                        session=args.session, env=args.env, stage=args.stage,
                        action="health_check", role=args.role,
                        instance=args.instance, instance_host=instance_host,
                        act_cfg=hc_cfg, tail_lines=args.tail_lines,
                        project_root=project_root, caller=args.caller,
                        wait_for_completion=True,
                    )
                    specs.append(hc_spec)

                spec = {"_multi_specs": specs, "action": "restart"}
            else:
                sys.stderr.write(
                    f"Error: action '{args.action}' not defined in "
                    f"environments.{args.env}.roles.{args.role}.actions\n"
                )
                return 1
        else:
            act_cfg = role_cfg["actions"][args.action]

            instance_obj = next(
                (i for i in (role_cfg.get("instances") or [])
                 if i.get("name") == args.instance),
                None,
            )
            if not instance_obj:
                sys.stderr.write(
                    f"Error: instance '{args.instance}' not found in "
                    f"environments.{args.env}.roles.<role>.instances\n"
                )
                return 1
            instance_host = instance_obj.get("host", "")

            spec = build_role_hook_spec(
                session=args.session,
                env=args.env,
                stage=args.stage,
                action=args.action,
                role=args.role,
                instance=args.instance,
                instance_host=instance_host,
                act_cfg=act_cfg,
                tail_lines=args.tail_lines,
                project_root=project_root,
                caller=args.caller,
                wait_for_completion=_resolve_wait_for_completion(args.action, args.wait_for_completion, act_cfg.get("wait_for_completion")),
            )

    # --log-dir 覆盖: 透传 PG_HOOK_LOG_DIR 到 hook (pg-run-hook.py:_PG_ENV_MAP 已映射)
    if args.log_dir:
        if "_multi_specs" in spec:
            for s in spec["_multi_specs"]:
                s["hook_log_dir"] = args.log_dir
                s["log_path"] = str(Path(args.log_dir) / Path(s["log_path"]).name)
                s["hook_result_path"] = str(Path(args.log_dir) / Path(s["hook_result_path"]).name)
        else:
            spec["hook_log_dir"] = args.log_dir
            spec["log_path"] = str(Path(args.log_dir) / Path(spec["log_path"]).name)
            spec["hook_result_path"] = str(Path(args.log_dir) / Path(spec["hook_result_path"]).name)

    # --timeout-override 覆盖: 输出 WARN (不阻止, ad-hoc 调试可用) 后替换
    if args.timeout_override is not None:
        sys.stderr.write(
            f"WARN: --timeout-override={args.timeout_override} 覆盖 "
            f"project.yaml timeout_seconds={spec.get('timeout_seconds')}\n"
        )
        if "_multi_specs" in spec:
            for s in spec["_multi_specs"]:
                s["timeout_seconds"] = args.timeout_override
        else:
            spec["timeout_seconds"] = args.timeout_override

    pg_hook_runner = (
        find_pg_skills_root(project_root)
        / "src" / "runtime" / "lib" / "pg-run-hook.py"
    )
    if not pg_hook_runner.is_file():
        sys.stderr.write(
            f"Error: pg-run-hook.py not found at {pg_hook_runner}\n"
        )
        return 2

    # multi-spec 路径: 顺序 fork-exec 每个子 spec, 任一失败早退
    if "_multi_specs" in spec:
        overall_ok = True
        sub_results = []
        for sub_spec in spec["_multi_specs"]:
            try:
                proc = subprocess.run(
                    ["python3", str(pg_hook_runner)],
                    input=json.dumps(sub_spec, indent=2),
                    text=True,
                    cwd=str(project_root),
                )
            except KeyboardInterrupt:
                return 0
            sub_results.append({
                "spec_role": sub_spec.get("role", ""),
                "spec_phase": sub_spec.get("phase", ""),
                "returncode": proc.returncode,
            })
            if proc.returncode != 0:
                overall_ok = False
                break
        # v3.12: restart_all_instances 复合 action 完成后, 写聚合 result.json
        # 与 _build_env_hook_plan (pg-build/bootstrap.py:648-651) 的 result_file 约定对齐。
        # pg-build 的 _verify_hook_executed 现在也会接受 dev-local-logs/role.* 日志
        # (即使本聚合文件缺失也能通过), 写此文件只是为了让 _build_env_hook_plan 约定的契约完整。
        result_file = os.environ.get("PG_RESULT_FILE", "")
        if result_file:
            try:
                aggregate = {
                    "status": "pass" if overall_ok else "fail",
                    "exit_code": 0 if overall_ok else 1,
                    "action": spec.get("action", "restart_all_instances"),
                    "sub_results": sub_results,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                }
                os.makedirs(os.path.dirname(result_file), exist_ok=True)
                with open(result_file, "w", encoding="utf-8") as f:
                    json.dump(aggregate, f, indent=2, ensure_ascii=False)
            except OSError as e:
                sys.stderr.write(f"WARN: failed to write aggregate result to {result_file}: {e}\n")
        return 0 if overall_ok else 1

    try:
        proc = subprocess.run(
            ["python3", str(pg_hook_runner)],
            input=json.dumps(spec, indent=2),
            text=True,
            cwd=str(project_root),
        )
    except KeyboardInterrupt:
        return 0
    return proc.returncode


def status_main(argv=None) -> int:
    """LLM-facing entry for prepare_env status query.

    Thin passthrough to pg-pipeline-runner.py prepare-env-status:
    - Validates --change (required) and --stage (optional)
    - Locates pg-pipeline-runner.py via find_project_root() + pg-skills layout
    - Spawns subprocess.run with stdout/stderr/exit code passthrough

    Output: identical to `pg-pipeline-runner.py prepare-env-status <change> [stage]`
    (JSON array of {stage, prepare:{status, log_path, message}} objects).
    """
    parser = argparse.ArgumentParser(
        prog="pg-invoke-hook.py status",
        description=(
            "Query prepare_env status for a change (and optional stage). "
            "Thin passthrough to pg-pipeline-runner.py prepare-env-status. "
            "Returns identical JSON output and exit code."
        ),
    )
    parser.add_argument("--change", required=True,
                        help="change name (positional arg to runner)")
    parser.add_argument("--stage", default=None,
                        help="optional stage name filter (positional arg to runner)")

    if argv is None:
        argv = sys.argv
    # argv layout: [program_name, "status", *flags]; auto-prepend if missing.
    if len(argv) < 2 or argv[1] != "status":
        argv = [argv[0], "status", *argv[1:]]
    args = parser.parse_args(argv[1:][1:])

    project_root = find_project_root()
    runner = (
        project_root
        / ".pg" / "skills" / "src" / "opencode" / "skills"
        / "pg-build" / "scripts" / "pg-pipeline-runner.py"
    )
    if not runner.is_file():
        sys.stderr.write(
            f"Error: pg-pipeline-runner.py not found at {runner}\n"
        )
        return 2

    cmd = ["python3", str(runner), "prepare-env-status", args.change]
    if args.stage:
        cmd.append(args.stage)

    proc = subprocess.run(cmd, cwd=str(project_root))
    return proc.returncode


def main(argv=None) -> int:
    """CLI entry dispatcher.

    Dispatches to invoke_hook_main() or status_main() based on the first
    positional subcommand. If no subcommand is given, default to invoke-hook
    for backward compatibility with the v3.2 thin-wrapper convention
    (`pg-invoke-hook.py <flags>` still works as `pg-invoke-hook.py invoke-hook <flags>`).
    """
    if argv is None:
        argv = sys.argv

    if len(argv) < 2:
        sys.stderr.write(
            "Usage:\n"
            "  pg-invoke-hook.py invoke-hook --change <C> --env <ENV> "
            "--role <ROLE> --instance <I> --action <A> [...]\n"
            "  pg-invoke-hook.py status --change <C> [--stage <S>]\n"
        )
        return 2

    subcommand = argv[1]
    if subcommand == "invoke-hook":
        return invoke_hook_main(argv)
    if subcommand == "status":
        return status_main(argv)

    # No subcommand or unknown subcommand: backward compat treats no
    # subcommand as invoke-hook (existing SKILL.md prompts use
    # `pg-invoke-hook.py <flags>` form).
    if subcommand.startswith("-"):
        return invoke_hook_main(argv)

    sys.stderr.write(
        f"Error: unknown subcommand '{subcommand}'\n"
        f"Valid subcommands: invoke-hook, status\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
