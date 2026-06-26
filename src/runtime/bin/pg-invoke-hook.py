#!/usr/bin/env python3
"""pg-invoke-hook.py — runtime 层统一 LLM-facing 入口 (env hooks + role actions).

抽取自 pg-pipeline-runner.py:cmd_invoke_hook (v3.1) 主体实现, 提升到 runtime 层后
供 pg-build / pg-fix-issue / pg-regression 三个 SKILL 共享调用. pg-pipeline-runner.py
保留同名子命令 (thin wrapper) 以保证向后兼容.

设计动机:
- pg-pipeline-runner.py 同时承担 "编排状态机" (next/record/check) 与
  "hook executor" (invoke-hook) 两类职责. 抽离后:
  * executor 归 runtime/bin/ (CLAUDE.md 仓库结构第 28-30 行预留位置)
  * SKILL 之间不再互相依赖 runner 路径
  * 测试可走 subprocess.run 黑盒, 不需 mock sys.argv

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
    --change <C> --env <ENV> --role <ROLE> --instance <I> --action <A> \\
    [--stage <S>] [--tail-lines <N>]

  python3 pg-invoke-hook.py invoke-hook \\
    --change <C> --env <ENV> --action prepare_env

  python3 pg-invoke-hook.py status --change <C> [--stage <S>]

Args:
  --change       change name (用于 log_path 路由 + status 状态查询)
  --env          environment name (必须在 project.yaml environments 中)
  --stage        stage name (默认: manual)
  --role         role name (backend/frontend/agent); per-role 必填, env-level 忽略
  --instance     instance name (必须在 environments.<env>.roles.<role>.instances 中);
                 per-role 必填, env-level 忽略
  --action       start|stop|logs|tail (per-role) 或 prepare_env|clean_env (env-level)
  --tail-lines   (logs/tail only) 追加 --tail-lines N 到 hook args 末尾

Spec 渲染 (与原 cmd_invoke_hook 100% 等价):
  cmd             = "bash " + shlex.quote(act_cfg["script"]) + (args if any)
  env vars 注入    = PG_CHANGE_NAME / PG_STAGE / PG_ENV / PG_ROLE / PG_INSTANCE_NAME /
                    PG_INSTANCE_HOST / PG_HOOK_TYPE / PG_SKILL_NAME
  timeout_seconds  = act_cfg["timeout_seconds"] (LLM 不传, runner 反查)
  log_path        = per-skill routing (see pg_log_dir_for_skill):
                    pg-build       -> .pg/changes/<change>/2-build/<env>/logs/...
                    pg-regression  -> .pg/regression/<suite>/<env>/logs/...
                    pg-fix-issue   -> .pg/fix-issue/<change>/<env>/logs/...
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
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
    if env_root and _has_config(env_root):
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


def pg_log_dir_for_skill(skill: str, change: str, env: str, project_root: Path) -> Path:
    """Return the per-skill log directory for hook logs.

    Routing rules (must stay in sync with .pg/hooks/lib/common.sh:pg_resolve_paths):
      pg-build       -> .pg/changes/<change>/2-build/<env>/logs
      pg-regression  -> .pg/regression/<suite>/<env>/logs   (strips leading "regression-")
      pg-fix-issue   -> .pg/fix-issue/<change>/<env>/logs
      兜底/empty     -> .pg/changes/<change>/2-build/<env>/logs (back-compat)
    """
    if skill == "pg-regression" and change.startswith("regression-"):
        suite = change[len("regression-"):]
        return project_root / ".pg" / "regression" / suite / env / "logs"
    if skill == "pg-fix-issue":
        return project_root / ".pg" / "fix-issue" / change / env / "logs"
    # pg-build + 兜底 (skill=="" / unknown) 全部走原 changes/<change>/2-build/<env>/logs
    return project_root / ".pg" / "changes" / change / "2-build" / env / "logs"


def build_env_level_hook_spec(
    change: str,
    env: str,
    stage: str,
    action: str,
    act_cfg: dict,
    project_root: Path,
    skill: str = "",
) -> dict:
    """Build pg-run-hook.py spec for environment-level hooks (prepare_env / clean_env).

    Environment-level hooks live directly under environments.<env>.<action>
    (NOT under roles.<role>.actions). They have no role/instance. We render
    a spec shape that pg-run-hook.py can consume: role/instance_host are
    empty strings; log_path is namespaced under env-level hooks subdir so
    it doesn't collide with role.* action logs.

    skill: optional skill name; injected as PG_SKILL_NAME via pg-run-hook.py.
    """
    rendered_args = []
    for raw in (act_cfg.get("args") or []):
        rendered_args.append(str(raw))

    inner_cmd = "bash " + shlex.quote(act_cfg["script"])
    if rendered_args:
        inner_cmd += " " + " ".join(shlex.quote(a) for a in rendered_args)

    log_path = str(
        pg_log_dir_for_skill(skill, change, env, project_root)
        / f"env.{action}.log"
    )

    spec = {
        "cmd": inner_cmd,
        "change": change,
        "stage": stage,
        "env": env,
        "role": "",
        "instance_name": "",
        "instance_host": "",
        "hook_type": action,
        "timeout_seconds": act_cfg.get("timeout_seconds"),
        "log_path": log_path,
    }
    if skill:
        spec["skill"] = skill
    return spec


def build_role_hook_spec(
    change: str,
    env: str,
    stage: str,
    action: str,
    role: str,
    instance: str,
    instance_host: str,
    act_cfg: dict,
    tail_lines,
    project_root: Path,
    skill: str = "",
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

    log_path = str(
        pg_log_dir_for_skill(skill, change, env, project_root)
        / f"role.{role}.{action}@{instance}.log"
    )

    spec = {
        "cmd": inner_cmd,
        "change": change,
        "stage": stage,
        "env": env,
        "role": role,
        "instance_name": instance,
        "instance_host": instance_host,
        "hook_type": action,
        "timeout_seconds": act_cfg.get("timeout_seconds"),
        "log_path": log_path,
    }
    if skill:
        spec["skill"] = skill
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

    timeout_seconds is read from project.yaml (NOT a CLI flag) and
    passed through to pg-run-hook.py.
    """
    parser = argparse.ArgumentParser(
        prog="pg-invoke-hook.py invoke-hook",
        description=(
            "Trigger a role action (start/stop/logs/tail) or env-level hook "
            "(prepare_env/clean_env) via pg-run-hook.py. Used by SKILL "
            "orchestrators (pg-build / pg-fix-issue / pg-regression); not part "
            "of any pipeline state machine."
        ),
    )
    parser.add_argument("--change", required=True,
                        help="change name (used for log_path routing)")
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
    parser.add_argument("--skill", default="",
                        help="skill name (e.g. pg-build, pg-regression); "
                             "injected as PG_SKILL_NAME via pg-run-hook.py")

    # argv layout: caller passes [program_name, "invoke-hook", *flags].
    # For test convenience we accept both [program, "invoke-hook", ...]
    # and [program, ...] (auto-prepend "invoke-hook" subcommand).
    if argv is None:
        argv = sys.argv
    if len(argv) < 2 or argv[1] != "invoke-hook":
        argv = [argv[0], "invoke-hook", *argv[1:]]
    args = parser.parse_args(argv[1:][1:])  # slice off program name AND "invoke-hook"

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
            change=args.change,
            env=args.env,
            stage=args.stage,
            action=args.action,
            act_cfg=env_hook_cfg,
            project_root=project_root,
            skill=args.skill,
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
                f"environments.{args.env}.roles.{args.role}.instances\n"
            )
            return 1
        instance_host = instance_obj.get("host", "")

        spec = build_role_hook_spec(
            change=args.change,
            env=args.env,
            stage=args.stage,
            action=args.action,
            role=args.role,
            instance=args.instance,
            instance_host=instance_host,
            act_cfg=act_cfg,
            tail_lines=args.tail_lines,
            project_root=project_root,
            skill=args.skill,
        )

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
