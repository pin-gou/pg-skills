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

v4 协议改造:
- --change → --session (canonical). --change 保留 1 版本作为 deprecated alias.
- --skill / --caller 硬缺省 'ad-hoc', 任何漏传 caller 的调用都落到 .pg/ad-hoc/.
- 新增 --log-dir (调试覆盖), --timeout-override (ad-hoc 调试, 输出 WARN).
- caller 维度路由:
    pg-build       -> .pg/changes/<session>/2-build/<env>/logs
    pg-regression  -> .pg/regression/<session>/<env>/logs
    pg-fix-issue   -> .pg/fix-issue/<session>/<env>/logs
    ad-hoc         -> .pg/ad-hoc/<session>/<env>/logs

顶级 subcommands:
- invoke-hook — 触发 role action (start/stop/logs/tail) 或 env-level hook
  (prepare_env/clean_env). 内部反查 project.yaml, 渲染 spec, 调 pg-run-hook.py.
- status     — 透传 prepare_env 状态查询到 pg-pipeline-runner.py
  prepare-env-status 子命令 (stdout JSON 透传, exit code 透传).
  LLM-facing 入口统一在 runtime 层, 与 invoke-hook 平级.

支持的动作 (仅 invoke-hook):
- per-role (需 --role + --instance):
  * start / stop / logs / tail
- env-level (忽略 --role/--instance):
  * prepare_env / clean_env

Usage:
  python3 pg-invoke-hook.py invoke-hook \\
    --session <S> --env <ENV> --role <ROLE> --instance <I> --action <A> \\
    [--stage <ST>] [--tail-lines <N>] [--skill pg-build|pg-regression|pg-fix-issue|ad-hoc] \\
    [--log-dir <DIR>] [--timeout-override <SECS>]

  python3 pg-invoke-hook.py invoke-hook \\
    --session <S> --env <ENV> --action prepare_env \\
    [--skill pg-build|pg-regression|pg-fix-issue|ad-hoc]

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

Spec 渲染 (v4):
  cmd             = "bash " + shlex.quote(act_cfg["script"]) + (args if any)
  env vars 注入    = PG_RUN_SESSION / PG_RUN_CALLER / PG_STAGE / PG_ENV /
                    PG_ROLE / PG_INSTANCE_NAME / PG_INSTANCE_HOST / PG_HOOK_TYPE
                    (PG_SKILL_NAME / PG_CHANGE_NAME 保留 1 版本作为 alias)
  timeout_seconds  = act_cfg["timeout_seconds"] (可被 --timeout-override 覆盖)
  log_path        = per-caller 路由 (see pg_log_dir_for_skill):
                    pg-build       -> .pg/changes/<session>/2-build/<env>/logs
                    pg-regression  -> .pg/regression/<session>/<env>/logs
                    pg-fix-issue   -> .pg/fix-issue/<session>/<env>/logs
                    ad-hoc         -> .pg/ad-hoc/<session>/<env>/logs
"""
from __future__ import annotations

import argparse
import json
import os
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
ENV_LEVEL_ACTIONS = ("prepare_env", "clean_env")

# Caller 维度枚举 (与 .pg/hooks/lib/common.sh:pg_resolve_paths 的 case 分支同步)
CALLER_PG_BUILD = "pg-build"
CALLER_PG_REGRESSION = "pg-regression"
CALLER_PG_FIX_ISSUE = "pg-fix-issue"
CALLER_AD_HOC = "ad-hoc"
KNOWN_CALLERS = (CALLER_PG_BUILD, CALLER_PG_REGRESSION, CALLER_PG_FIX_ISSUE, CALLER_AD_HOC)


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
      pg-build       -> .pg/changes/<session>/2-build/<env>/logs
      pg-regression  -> .pg/regression/<session>/<env>/logs   (session 已含 regression- 前缀 + date + seq)
      pg-fix-issue   -> .pg/fix-issue/<session>/<env>/logs    (session 已含 fix- 前缀)
      ad-hoc         -> .pg/ad-hoc/<session>/<env>/logs       (独立顶级目录, 不与 SKILL 命名空间混)
    """
    base = project_root / ".pg"
    if caller == CALLER_PG_BUILD:
        return base / "changes" / session / "2-build" / env / "logs"
    if caller == CALLER_PG_REGRESSION:
        return base / "regression" / session / env / "logs"
    if caller == CALLER_PG_FIX_ISSUE:
        return base / "fix-issue" / session / env / "logs"
    # ad-hoc
    return base / "ad-hoc" / session / env / "logs"


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

    caller: 调用方身份 (pg-build / pg-regression / pg-fix-issue / ad-hoc).
            注入为 PG_RUN_CALLER via pg-run-hook.py (PG_SKILL_NAME 作为 1 版本兼容 alias).
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
    }
    return spec


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
) -> dict:
    """Build pg-run-hook.py spec for per-role actions (start/stop/logs/tail)."""
    rendered_args = []
    for raw in (act_cfg.get("args") or []):
        a = str(raw)
        a = a.replace("{role}", role)
        a = a.replace("{instance.name}", instance)
        a = a.replace("{instance.host}", instance_host)
        rendered_args.append(a)

    # Option Y: --tail-lines is appended to hook args list (logs/tail only).
    if action in ("logs", "tail") and tail_lines is not None:
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
    """LLM-facing entry point for triggering role actions (start/stop/logs/tail)
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
            "Trigger a role action (start/stop/logs/tail) or env-level hook "
            "(prepare_env/clean_env) via pg-run-hook.py. Used by SKILL "
            "orchestrators (pg-build / pg-fix-issue / pg-regression) and by "
            "agent ad-hoc / pg-run manual calls. NOT part of any pipeline state "
            "machine."
        ),
    )
    parser.add_argument("--session", default="",
                        help=(
                            "session 名 (与 caller 正交). "
                            "pg-build: 提案名; pg-regression: regression-<suite>-<date>-<seq>; "
                            "pg-fix-issue: fix-<date>-<slug>; "
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
                            "for per-role actions (start/stop/logs/tail); "
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
                        choices=["start", "stop", "logs", "tail",
                                 "prepare_env", "clean_env"],
                        help=(
                            "action to trigger. start/stop/logs/tail are "
                            "per-role lifecycle actions (require --role and "
                            "--instance); prepare_env/clean_env are "
                            "environment-level lifecycle hooks (ignore "
                            "--role/--instance)."
                        ))
    parser.add_argument("--tail-lines", type=int, default=None,
                        help="(logs/tail only) append --tail-lines N to hook args")
    parser.add_argument("--skill", "--caller", dest="caller", default=CALLER_AD_HOC,
                        choices=list(KNOWN_CALLERS),
                        help=(
                            "调用方身份 (caller 维度路由). "
                            "硬缺省 'ad-hoc' — 任何不显式传 --skill 的调用都视为 ad-hoc, "
                            "日志落到 .pg/ad-hoc/<session>/<env>/logs/."
                            "SKILL (pg-build / pg-regression / pg-fix-issue) 必须显式标注."
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

    # Environment-level lifecycle hooks (prepare_env / clean_env) are
    # NOT role-scoped: they live directly under environments.<env>.
    if args.action in ENV_LEVEL_ACTIONS:
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
        # Per-role lifecycle action (start / stop / logs / tail).
        if args.role not in (env_cfg.get("roles") or {}):
            sys.stderr.write(
                f"Error: role '{args.role}' not defined in environments.{args.env}.roles\n"
            )
            return 1
        role_cfg = env_cfg["roles"][args.role]
        if args.action not in (role_cfg.get("actions") or {}):
            sys.stderr.write(
                f"Error: action '{args.action}' not defined in "
                f"environments.{args.env}.roles.{args.role}.actions\n"
            )
            return 1
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
        )

    # --log-dir 覆盖: 透传 PG_HOOK_LOG_DIR 到 hook (pg-run-hook.py:_PG_ENV_MAP 已映射)
    if args.log_dir:
        spec["hook_log_dir"] = args.log_dir
        spec["log_path"] = str(Path(args.log_dir) / Path(spec["log_path"]).name)
        spec["hook_result_path"] = str(Path(args.log_dir) / Path(spec["hook_result_path"]).name)

    # --timeout-override 覆盖: 输出 WARN (不阻止, ad-hoc 调试可用) 后替换
    if args.timeout_override is not None:
        sys.stderr.write(
            f"WARN: --timeout-override={args.timeout_override} 覆盖 "
            f"project.yaml timeout_seconds={spec.get('timeout_seconds')}\n"
        )
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

    proc = subprocess.run(
        ["python3", str(pg_hook_runner)],
        input=json.dumps(spec, indent=2),
        text=True,
        cwd=str(project_root),
    )
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
