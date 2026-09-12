#!/usr/bin/env python3
"""pg-parse-config.py - Unified configuration provider for pg-* SKILLs.

Reads .pg/project.yaml as the single source of truth.
Manager calls this with a workflow name to get only the config
that workflow needs — preventing context pollution in sub-agents.

Usage:
  python3 pg-parse-config.py <workflow>               # Filtered by workflow
  python3 pg-parse-config.py                          # Full config (debug)
  python3 pg-parse-config.py --key backend.port       # Single value
  python3 pg-parse-config.py --prefix backend         # Subtree as JSON

  Validation:
  After producing the config JSON on stdout, this script validates that
  every `bash <path>.sh` reference inside any track/phase command field
  points to a file that actually exists. If any referenced script is
  missing, the script writes a `VALIDATION BLOCKING:` report to stderr
  and exits with code 1. This causes the calling bash command to fail,
  so the LLM naturally stops and the user must fix .pg/project.yaml
  before retrying. The stdout JSON is still emitted (for backward
  compatibility) but downstream code should not run.
"""

import json
import os
import re
import shlex
import sys

try:
    import yaml
except ImportError:
    print('{"error": "PyYAML is required. Install with: pip install pyyaml"}', file=sys.stderr)
    sys.exit(1)

CONFIG_PATH_CANDIDATES = [
    # Phase 2+: 从脚本位置 / cwd 向上查找 .pg/project.yaml (与
    # pg_pipeline_common.find_project_root 同策略, 不依赖脚本嵌套深度)
    lambda script_dir: _find_project_yaml_upward(script_dir),
    lambda _script_dir: _find_project_yaml_upward(os.getcwd()),
]


def _find_project_yaml_upward(start_dir):
    """向上遍历目录树查找 .pg/project.yaml, 找到返回路径, 否则返回 None."""
    current = os.path.abspath(start_dir)
    while True:
        candidate = os.path.join(current, ".pg", "project.yaml")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _resolve_config_path():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for candidate_fn in CONFIG_PATH_CANDIDATES:
        path = candidate_fn(script_dir)
        if path and os.path.exists(path):
            return path
    # Fallback: 显式报错路径 (load() 处抛 FileNotFoundError, 与旧行为一致)
    return os.path.join(script_dir, "project.yaml")

CONFIG_PATH = _resolve_config_path()

# Each workflow only gets the top-level config keys it needs.
# Add new entries when creating pg-* SKILLs.
# v3.0: 4 段新结构 (modules / environments / tracks / stages).
# deployments 已合并到 environments.actions per-role + cross-role 中.
# pipeline / testSuites / port / rebuild_and_restart / health_check 已废弃,
# 不再列入任何 workflow.
WORKFLOW_KEYS = {
    # pg-agent: LLM agent 通用的 SSOT 查询入口 (pg-auto-pilot 自动驾驶模式的
    # 唯一查询模式). 只暴露 modules + environments, 不暴露 tracks / stages 等
    # skill 内部状态. agent 走 --resolve-* / --key / --prefix 取细粒度值.
    "pg-agent": ["modules", "environments"],
}



# Command-bearing fields that may reference bash scripts. Used to scan
# every module's build/lint/test commands for `bash <path>.sh` invocations.
COMMAND_FIELDS = (
    "build", "lint",
)

# Regex: match `bash <path>.sh` where path is non-whitespace and not a
# shell operator. Captures the script path in group 1.
BASH_SCRIPT_RE = re.compile(r"\bbash\s+([^\s|&;]+\.sh)\b")


def load():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    _validate_roles_uniqueness(data)
    return data


def _validate_roles_uniqueness(data):
    """校验 environments.<env>.roles 数组内 name 字段唯一.

    roles 改为 array 形态后, 重复的 role 名不再被 YAML key 唯一性自动拒绝,
    必须在 reader 入口加运行时检查. 失败抛 ValueError (上游 doctor 捕获).
    """
    environments = (data or {}).get("environments") or {}
    for env_name, env_cfg in environments.items():
        roles = (env_cfg or {}).get("roles") or []
        if not isinstance(roles, list):
            raise ValueError(
                f"environments.{env_name}.roles must be an array (got {type(roles).__name__})"
            )
        seen = set()
        for r in roles:
            if not isinstance(r, dict):
                raise ValueError(
                    f"environments.{env_name}.roles[*] must be a mapping "
                    f"(got {type(r).__name__})"
                )
            name = r.get("name", "")
            if not name:
                raise ValueError(
                    f"environments.{env_name}.roles[*].name is required (each role must declare name)"
                )
            if name in seen:
                raise ValueError(
                    f"environments.{env_name}.roles: duplicate role name {name!r}"
                )
            seen.add(name)


def get_by_path(data, path):
    parts = path.split(".")
    current = data
    for p in parts:
        if isinstance(current, dict):
            if p in current:
                current = current[p]
            else:
                return None
        elif isinstance(current, list):
            try:
                idx = int(p)
            except ValueError:
                return None
            if 0 <= idx < len(current):
                current = current[idx]
            else:
                return None
        else:
            return None
    return current


def compute_resolved_actions(environments):
    """Resolve template variables in action definitions to flat command strings.

    Replaces {role}, {instance.name}, {instance.host} in each action's args
    with actual values from the environment's instance topology.

    Returns a dict keyed by "{env}.{role}.{instance}.{action}" with
    {"cmd": "bash <script> <arg1> <arg2> ...", "timeout_seconds": N}.
    """
    resolved = {}
    if not environments:
        return resolved
    for env_name, env_cfg in environments.items():
        roles = env_cfg.get("roles") or []
        for role in roles:
            role_name = role.get("name", "")
            role_cfg = role
            instances = role_cfg.get("instances") or []
            actions = role_cfg.get("actions") or {}
            for instance in instances:
                inst_name = instance.get("name", "")
                inst_host = instance.get("host", "")
                for act_name, act_cfg in actions.items():
                    script = act_cfg.get("script", "")
                    args = []
                    for arg in (act_cfg.get("args") or []):
                        if isinstance(arg, str):
                            arg = arg.replace("{role}", role_name)
                            arg = arg.replace("{instance.name}", inst_name)
                            arg = arg.replace("{instance.host}", inst_host)
                            arg = arg.replace("{lines:100}", "100")
                        args.append(arg)
                    parts = [script] + args
                    cmd = "bash " + " ".join(parts) if parts else script
                    key = f"{env_name}.{role_name}.{inst_name}.{act_name}"
                    entry = {"cmd": cmd}
                    timeout = act_cfg.get("timeout_seconds")
                    if timeout is not None:
                        entry["timeout_seconds"] = timeout
                    resolved[key] = entry
    return resolved


def resolve_module_command(modules, module_name, field, test_key=None):
    """Resolve a single module command entry to a runnable form.

    Reuses the same timeout normalization + rendering as the runtime hook
    runner so the two paths never drift. The
    returned shape is the flat dict pg-run-hook.py accepts:

        {"cmd": "timeout N bash -c '<shell>'", "timeout_seconds": N}

    Args:
        modules: the `modules` section of .pg/project.yaml (dict of
            module name -> module config).
        module_name: name of the module to look up.
        field: "build" | "lint" | "test" — which command slot to resolve.
        test_key: required when field == "test", the test_key (unit /
            integration / e2e / etc.).

    Returns:
        dict {"cmd": str, "timeout_seconds": int} on success.
        None if module_name not found, field missing, or (for test)
            test_key not defined. Callers should treat None as "this
            module/field does not apply" (no command to run).
    """
    if not modules or module_name not in modules:
        return None
    mod = modules[module_name] or {}
    module_default_timeout = mod.get("timeout_seconds")

    if field == "test":
        tests = mod.get("test") or {}
        if test_key is None or test_key not in tests:
            return None
        entry = tests[test_key]
    else:
        if field not in mod:
            return None
        entry = mod[field]

    if not entry:
        return None

    # Inline normalized module command (避免跨仓 sibling import).
    # 原实现在 pg_pipeline_common.normalize_module_command + render_module_command
    # Phase 2 抽到 pg-skills 后, pg-parse-config.py 不再 sibling import
    if isinstance(entry, str):
        cmd = entry
        timeout = None
    elif isinstance(entry, dict):
        if "cmd" not in entry:
            raise ValueError(
                f"Module command object missing required 'cmd' field: {entry!r}")
        cmd = entry["cmd"]
        if not isinstance(cmd, str) or not cmd.strip():
            raise ValueError(
                f"Module command 'cmd' must be a non-empty string: {entry!r}")
        timeout = entry.get("timeout_seconds")
    else:
        raise ValueError(
            f"Module command entries must be string or dict; got "
            f"{type(entry).__name__}: {entry!r}")
    if timeout is None:
        timeout = module_default_timeout
    if timeout is None:
        timeout = 1800  # schema default
    return {
        "cmd": f"timeout {timeout} bash -c {shlex.quote(cmd)}"
            if timeout is not None else cmd,
        "timeout_seconds": timeout,
    }


def filter_by_workflow(data, workflow):
    keys = WORKFLOW_KEYS.get(workflow)
    if keys is None:
        return data
    # resolved_actions is computed, not a raw key
    has_resolved = "resolved_actions" in keys
    result_keys = [k for k in keys if k != "resolved_actions"]
    result = {k: data[k] for k in result_keys if k in data}
    if has_resolved:
        envs = data.get("environments") or {}
        result["resolved_actions"] = compute_resolved_actions(envs)
    return result


def inject_meta(data):
    import socket
    data["__meta"] = {"hostname": socket.gethostname()}
    return data


def emit_cwd_policy_notice(json_only: bool = False):
    """每次解析配置都输出 cwd 规约提示（stderr，模型必看）。

    v2.0 核心规约：所有命令从项目根路径执行，executor 不会自动切换 cwd。

    v2.0.1 新增 json_only 参数：传 True 时抑制 banner 输出，让 stdout 纯净，
    便于下游（LLM/SKILL）直接 json.load() 而无需手动截取首个 { 之后的 JSON。
    对应 --json-only 命令行 flag。
    """
    if json_only:
        return
    notice = """\
============================================================
[pg-parse-config] 命令执行位置规约 (v2.0)
============================================================
所有命令从项目根路径执行（executor 不会自动切换 cwd）:
  - 需切换目录的命令在命令字符串中显式写 'cd <dir> && <cmd>'
  - rebuild_and_restart / verify 脚本应自包含 cwd 处理
    （脚本内部用 cd "$(dirname "$0")/../<track>" 等）

示例:
  rebuild_and_restart: bash scripts/agent-update.sh    # 脚本内部自己 cd
  test: cd <module-name> && go test ./...             # 命令内显式 cd
  verify: bash scripts/agent-verify-running.sh         # 脚本内部处理
============================================================
"""
    print(notice, file=sys.stderr)


def find_script_candidates(script_path, track_root):
    """Return candidate absolute-or-project-relative paths for a script.

    The conventional layout in .pg/project.yaml uses three patterns:
      1. bare relative path (resolved against project root)
      2. `<root>/<script>` (resolved against the track root, e.g. when
         the user runs the command from the project root with the track
         as the working directory)
      3. `<root>/../<script>` (the `cd <root> && bash ../scripts/...`
         convention used by backend/frontend)
    """
    root = (track_root or "").rstrip("/")
    return [
        script_path,
        os.path.join(root, script_path) if root else script_path,
        os.path.join(root, "..", script_path) if root else script_path,
    ]


def validate_scripts(data):
    """Walk every track/phase command field and verify bash script paths.

    Returns a list of error dicts. Empty list means all references resolve.
    Non-bash commands (mvn, go, pnpm, curl, ...) are skipped because
    their target is a system tool, not a file we can existence-check.
    """
    errors = []
    tracks = ((data.get("pipeline") or {}).get("tracks") or {})
    for tid, t in tracks.items():
        if not isinstance(t, dict):
            continue
        root = t.get("root")
        for field in COMMAND_FIELDS:
            cmd = t.get(field)
            if isinstance(cmd, list):
                cmds_to_check = [c for c in cmd if isinstance(c, str)]
            elif isinstance(cmd, str):
                cmds_to_check = [cmd]
            else:
                continue
            for cmd_str in cmds_to_check:
                for m in BASH_SCRIPT_RE.finditer(cmd_str):
                    script = m.group(1)
                    candidates = find_script_candidates(script, root)
                    if not any(os.path.exists(c) for c in candidates):
                        errors.append({
                            "track": tid,
                            "field": field,
                            "script": script,
                            "candidates": candidates,
                        })
    return errors


def main():
    data = load()
    args = sys.argv[1:]

    # v2.0.1 新增: --json-only 抑制 banner, 让 stdout 纯净 (LLM 直接 json.load)
    json_only = False
    if "--json-only" in args:
        json_only = True
        args = [a for a in args if a != "--json-only"]

    if not args:
        print(json.dumps(inject_meta(data), indent=2, ensure_ascii=False))
        emit_cwd_policy_notice(json_only=json_only)
        _run_validation(data)
        return

    # First positional arg as workflow name
    if args[0] in WORKFLOW_KEYS:
        sub_args = args[1:]
        filtered = filter_by_workflow(data, args[0])

        print(json.dumps(inject_meta(filtered), indent=2, ensure_ascii=False))
        emit_cwd_policy_notice(json_only=json_only)
        _run_validation(data)
        return

    i = 0
    while i < len(args):
        if args[i] == "--key" and i + 1 < len(args):
            val = get_by_path(data, args[i + 1])
            print(json.dumps(val, ensure_ascii=False))
            i += 2
        elif args[i] == "--prefix" and i + 1 < len(args):
            val = get_by_path(data, args[i + 1])
            print(json.dumps(val, ensure_ascii=False))
            i += 2
        elif args[i] == "--resolve-module-build" and i + 1 < len(args):
            result = resolve_module_command(
                data.get("modules") or {}, args[i + 1], "build")
            print(json.dumps(result, ensure_ascii=False))
            i += 2
        elif args[i] == "--resolve-module-lint" and i + 1 < len(args):
            result = resolve_module_command(
                data.get("modules") or {}, args[i + 1], "lint")
            print(json.dumps(result, ensure_ascii=False))
            i += 2
        elif args[i] == "--resolve-module-test" and i + 2 < len(args):
            result = resolve_module_command(
                data.get("modules") or {}, args[i + 1], "test",
                test_key=args[i + 2])
            print(json.dumps(result, ensure_ascii=False))
            i += 3
        elif args[i] == "--resolve-env" and i + 1 < len(args):
            env_name = args[i + 1]
            envs = data.get("environments") or {}
            if env_name not in envs:
                print(json.dumps(
                    {"error": f"environment not found: {env_name}",
                     "available": list(envs.keys())},
                    ensure_ascii=False))
            else:
                resolved = compute_resolved_actions({env_name: envs[env_name]})
                print(json.dumps(
                    {"name": env_name, "resolved_actions": resolved},
                    ensure_ascii=False))
            i += 2
        else:
            print(json.dumps({"error": f"Unknown argument: {args[i]}"}, ensure_ascii=False))
            i += 1


def _run_validation(data):
    """Emit validation report to stderr and exit non-zero on failure.

    Called after stdout output to keep behavior observable for callers
    that pipe/redirect stdout. The validation is blocking: any missing
    script aborts the workflow via non-zero exit so the LLM stops.
    """
    errors = validate_scripts(data)
    if errors:
        print("VALIDATION BLOCKING: config validation failed:",
              file=sys.stderr)
        for e in errors:
            if "script" in e:
                print(
                    f"  - track={e['track']} field={e['field']} script={e['script']} "
                    f"(candidates tried: {e['candidates']})",
                    file=sys.stderr,
                )
            else:
                print(
                    f"  - field={e['field']} value={e.get('value', '')} reason={e['reason']}",
                    file=sys.stderr,
                )
        print(
            "\nFix .pg/project.yaml so every `bash <path>.sh` reference "
            "points to an existing script, then re-run.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
