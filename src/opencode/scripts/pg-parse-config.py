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
import subprocess
import sys

try:
    import yaml
except ImportError:
    print('{"error": "PyYAML is required. Install with: pip install pyyaml"}', file=sys.stderr)
    sys.exit(1)

CONFIG_PATH_CANDIDATES = [
    # Modern: .pg/project.yaml (Phase 2+)
    lambda script_dir: os.path.normpath(os.path.join(script_dir, "../../../../project.yaml")),
    # Legacy: .pg/project.yaml (Phase 1 双轨期)
    lambda script_dir: os.path.normpath(os.path.join(script_dir, "../../.pg/project.yaml")),
]

def _resolve_config_path():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for candidate_fn in CONFIG_PATH_CANDIDATES:
        path = candidate_fn(script_dir)
        if os.path.exists(path):
            return path
    # Fallback to first candidate (will raise FileNotFoundError downstream)
    return CONFIG_PATH_CANDIDATES[0](script_dir)

CONFIG_PATH = _resolve_config_path()

# Each workflow only gets the top-level config keys it needs.
# Add new entries when creating pg-* SKILLs.
# v3.0: 4 段新结构 (modules / environments / tracks / stages) + fix_issue.
# deployments 已合并到 environments.actions per-role + cross-role 中.
# pipeline / testSuites / port / rebuild_and_restart / health_check 已废弃,
# 不再列入任何 workflow.
# fix_issue 段仅 pg-fix-issue 工作流可见, 描述主 agent 整体修复迭代 (与 tracks.max_fix_retries 区分).
WORKFLOW_KEYS = {
    "pg-build": ["modules", "environments", "tracks", "stages",
                          "git", "build"],
    "pg-verify-and-merge": ["modules", "tracks", "stages",
                              "git", "flyway", "verify_merge"],
    "pg-propose": ["modules", "tracks", "stages", "propose"],
    # pg-fix-issue v3.0: resolved_actions removed — service 启停统一由
    # pg-invoke-hook.py invoke-hook 渲染, parser 不再预渲染.
    # v3.2: 渲染从 pg-pipeline-runner.py invoke-hook 抽到 runtime 层独立 CLI
    # pg-invoke-hook.py (pg-pipeline-runner.py 保留 thin wrapper 兼容).
    "pg-fix-issue": ["modules", "environments", "tracks", "stages", "fix_issue"],
    "pg-quick-build": ["modules", "environments", "tracks", "stages", "git"],
    "pg-regression": ["modules", "environments", "regression"],
    # pg-agent: LLM agent 通用的 SSOT 查询入口. 只暴露 modules + environments,
    # 不暴露 tracks / stages / fix_issue 等 skill 内部状态. agent 走 --resolve-* /
    # --key / --prefix 取细粒度值, 不要用带 skill 名的 workflow (那是给 skill
    # 编排器用的, agent 用会被迫看到噪声).
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
        return yaml.safe_load(f)


def get_by_path(data, path):
    parts = path.split(".")
    current = data
    for p in parts:
        if isinstance(current, dict) and p in current:
            current = current[p]
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
        roles = env_cfg.get("roles") or {}
        for role_name, role_cfg in roles.items():
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

    Reuses the same timeout normalization + rendering as the pg-build
    runner (_build_module_context) so the two paths never drift. The
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


def _filter_regression_by_suite(raw_data, filtered, suite_name):
    """Deep-filter pg-regression output to only one suite.

    Keeps only:
      - regression.suite → the named suite
      - modules         → the module used by that suite
      - environments    → the environment used by that suite
    """
    suites = (raw_data.get("regression") or {}).get("suite") or {}
    suite_cfg = suites.get(suite_name)
    if not suite_cfg:
        return filtered

    mod = suite_cfg.get("module")
    env_name = (suite_cfg.get("environment") or {}).get("name")

    out = {}
    if "regression" in filtered:
        out["regression"] = {"suite": {suite_name: suite_cfg}}
    if mod and "modules" in filtered and mod in filtered["modules"]:
        out["modules"] = {mod: filtered["modules"][mod]}
    if env_name and "environments" in filtered and env_name in filtered["environments"]:
        out["environments"] = {env_name: filtered["environments"][env_name]}
    out["__meta"] = filtered.get("__meta", {})
    return out


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


def validate_regression(data):
    """Validate regression.suite schema with 7 hard rules.

    Schema (each suite):
      regression.suite.<name>:
        environment:                       # required
          name: <env-name>                 # ∈ environments
          required_roles: [role, ...]      # ∈ environments.<env>.roles
        module: <module-id>                # ∈ modules
        test_keys: [key, ...]              # non-empty, each ∈ modules.<m>.test.*

    Hard rules:
      1. regression.suite must exist and be non-empty
      2. each suite must have module / test_keys / environment.name / environment.required_roles
      3. suite.module ∈ modules
      4. suite.test_keys[i] ∈ modules.<m>.test.*
      5. suite.environment.name ∈ environments
      6. suite.environment.required_roles[j] ∈ environments.<env>.roles
      7. top-level regression.environment is FORBIDDEN (anti-leftover)

    Returns a list of error dicts. Empty list means config is valid.
    """
    errors = []
    reg = data.get("regression")
    if not reg:
        errors.append({
            "field": "regression",
            "reason": "missing section; add 'regression:\\n  suite:\\n    <name>: ...' to .pg/project.yaml",
        })
        return errors

    # Rule 7: hard-block any leftover top-level environment
    if "environment" in reg:
        errors.append({
            "field": "regression.environment",
            "reason": "forbidden; move to regression.suite.<s>.environment.name (no global default)",
        })

    suites = reg.get("suite")
    if not suites or not isinstance(suites, dict) or not suites:
        errors.append({
            "field": "regression.suite",
            "reason": "missing or empty; declare at least one suite",
        })
        return errors

    modules = data.get("modules") or {}
    environments = data.get("environments") or {}

    for suite_name, suite_cfg in suites.items():
        if not isinstance(suite_cfg, dict):
            errors.append({
                "field": f"regression.suite.{suite_name}",
                "reason": "must be a mapping (suite definition)",
            })
            continue

        # Rule 2: required fields
        for required in ("module", "test_keys", "environment"):
            if required not in suite_cfg:
                errors.append({
                    "field": f"regression.suite.{suite_name}.{required}",
                    "reason": "missing required field",
                })

        # Rule 3: module ∈ modules
        module = suite_cfg.get("module")
        if module and module not in modules:
            errors.append({
                "field": f"regression.suite.{suite_name}.module",
                "value": module,
                "reason": f"not found in modules: {list(modules.keys())}",
            })

        # Rule 4: test_keys non-empty list, each ∈ modules.<m>.test.*
        test_keys = suite_cfg.get("test_keys")
        if isinstance(test_keys, list) and test_keys:
            if module in modules:
                module_test = modules[module].get("test") or {}
                for tk in test_keys:
                    if tk not in module_test:
                        errors.append({
                            "field": f"regression.suite.{suite_name}.test_keys",
                            "value": tk,
                            "reason": f"not found in modules.{module}.test: {list(module_test.keys())}",
                        })
        elif test_keys is not None and (not isinstance(test_keys, list) or not test_keys):
            errors.append({
                "field": f"regression.suite.{suite_name}.test_keys",
                "reason": "must be a non-empty list",
            })

        # Rule 5 & 6: environment.name ∈ environments, required_roles ∈ env.roles
        env_cfg = suite_cfg.get("environment")
        if isinstance(env_cfg, dict):
            env_name = env_cfg.get("name")
            if env_name:
                if env_name not in environments:
                    errors.append({
                        "field": f"regression.suite.{suite_name}.environment.name",
                        "value": env_name,
                        "reason": f"not found in environments: {list(environments.keys())}",
                    })
                else:
                    # Rule 6: required_roles ⊆ env.roles
                    required_roles = env_cfg.get("required_roles")
                    if isinstance(required_roles, list):
                        env_roles = (environments[env_name].get("roles") or {})
                        for role in required_roles:
                            if role not in env_roles:
                                errors.append({
                                    "field": f"regression.suite.{suite_name}.environment.required_roles",
                                    "value": role,
                                    "reason": f"not found in environments.{env_name}.roles: {list(env_roles.keys())}",
                                })
                    elif required_roles is not None:
                        errors.append({
                            "field": f"regression.suite.{suite_name}.environment.required_roles",
                            "reason": "must be a list (use [] for unit tests)",
                        })
        elif env_cfg is not None:
            errors.append({
                "field": f"regression.suite.{suite_name}.environment",
                "reason": "must be a mapping with 'name' and 'required_roles'",
            })

    return errors


def validate_track_suite_mapping(data):
    """Warn (non-blocking) when a pipeline track has no matching testSuite.

    Convention: testSuite 名称 must equal pipeline.tracks.<name>. This lets
    pg-verify-and-merge map an incoming AffectedTracks list to testSuites
    without a separate pathPatterns config.

    Returns a list of warning dicts. Empty list means the mapping is consistent.
    Non-bash references and known phase-only entries (e.g. proto-compile,
    openapi-gen) are acceptable to skip — we only warn for tracks of type
    "track" (which typically have real test coverage).
    """
    warnings = []
    pipeline = (data.get("pipeline") or {})
    tracks = pipeline.get("tracks") or {}
    test_suites = data.get("testSuites") or {}
    order = pipeline.get("order") or []

    for track_name in order:
        if track_name in test_suites:
            continue
        track_def = tracks.get(track_name)
        if not isinstance(track_def, dict):
            continue
        # Only warn for full tracks (not "phase" entries which are single-step).
        if track_def.get("type") != "track":
            continue
        warnings.append({
            "track": track_name,
            "reason": (
                f"track '{track_name}' (type=track) has no matching entry "
                f"in testSuites; pg-verify-and-merge will skip tests for this track"
            ),
        })
    return warnings


# ============================================================
# pg-verify-and-merge subcommand support
# ============================================================

TRACK_HEADING_RE = re.compile(r"^##\s+\d+\.\s+\S+\.(\S+)\s+")


def _is_simple_track(config, track_id):
    """True if tracks.<track_id>.type == 'simple'."""
    track_cfg = (config.get("tracks") or {}).get(track_id) or {}
    return track_cfg.get("type") == "simple"


def _parse_tasks_md_track_ids(tasks_md_path):
    """Extract track_ids from `## {N}. {stage}.{track_id}` headings in tasks.md.

    Returns a list preserving source order, deduplicated (first occurrence).
    """
    if not os.path.isfile(tasks_md_path):
        return []
    seen = set()
    result = []
    with open(tasks_md_path, encoding="utf-8") as f:
        for line in f:
            m = TRACK_HEADING_RE.match(line)
            if not m:
                continue
            tid = m.group(1).split(":", 1)[0]
            if tid in seen:
                continue
            seen.add(tid)
            result.append(tid)
    return result


def _parse_manifest_track_ids(manifest_path):
    """Extract qualified track_ids from execution-manifest.yaml.

    Parses stages[].tracks[].id and prefixes with stage name.
    Excludes final_gate section (not a track).
    Returns a list preserving manifest order, deduplicated.
    """
    if not os.path.isfile(manifest_path):
        return []
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = yaml.safe_load(f) or {}
        result = []
        seen = set()
        for stage in manifest.get("stages", []):
            stage_name = stage.get("name", "")
            for t in stage.get("tracks", []):
                tid = t["id"] if isinstance(t, dict) else t
                qualified = f"{stage_name}.{tid}" if stage_name else tid
                if qualified not in seen:
                    seen.add(qualified)
                    result.append(qualified)
        return result
    except Exception:
        return []


def _git_diff_names(default_branch):
    """Return the set of changed file paths between origin/<default> and HEAD.

    Returns None on git error (caller falls back). Empty set means no diff.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"origin/{default_branch}", "HEAD"],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _infer_affected_tracks(config, change_dir, default_branch, explicit=None):
    """Infer the set of tracks affected by a change.

    4-layer fallback (first hit wins, simple tracks always filtered):
      1. explicit (CLI --affected-tracks) → use as-is, still filter simple
      2. execution-manifest.yaml → tracks in stages[].tracks[].id (already filtered by gen script)
      3. tasks.md ## headings → dedup, filter simple
      4. git diff → tracks.<t>.root path prefix match, filter simple
      5. regression.suite keys → filter simple (last resort)

    Returns a list of track_id strings, in first-seen order.
    """
    if explicit:
        candidate_iter = (t.strip() for t in explicit.split(",") if t.strip())
        return [t for t in candidate_iter if not _is_simple_track(config, t)]

    manifest_path = os.path.join(change_dir, "execution-manifest.yaml")
    manifest_tracks = _parse_manifest_track_ids(manifest_path)
    if manifest_tracks:
        return [t for t in manifest_tracks if not _is_simple_track(config, t)]

    tasks_md = os.path.join(change_dir, "tasks.md")
    track_ids = _parse_tasks_md_track_ids(tasks_md)
    if track_ids:
        return [t for t in track_ids if not _is_simple_track(config, t)]

    if default_branch:
        diff_files = _git_diff_names(default_branch)
        if diff_files is not None:
            matched = []
            seen = set()
            for t_name, t_cfg in (config.get("tracks") or {}).items():
                if _is_simple_track(config, t_name):
                    continue
                root = (t_cfg.get("root") or t_cfg.get("modules") or [])
                if isinstance(root, str):
                    roots = [root]
                else:
                    roots = []
                    for m in root:
                        mroot = ((config.get("modules") or {}).get(m) or {}).get("root")
                        if mroot:
                            roots.append(mroot)
                if any(f.startswith(r.rstrip("/")) for r in roots for f in diff_files):
                    if t_name not in seen:
                        matched.append(t_name)
                        seen.add(t_name)
            if matched:
                return matched

    suites = (config.get("regression") or {}).get("suite") or {}
    return [s for s in suites.keys() if not _is_simple_track(config, s)]


def _infer_output_format(module_cfg, test_key):
    """Infer test result parser format from module language + test_key.

    test_key takes precedence (e2e is always playwright regardless of language).
    Falls back to module.language mapping; defaults to 'shell' for unknown.
    """
    if test_key == "e2e":
        return "playwright"
    language = (module_cfg or {}).get("language", "")
    return {
        "java":       "maven-surefire",
        "go":         "go-test",
        "typescript": "shell",
        "python":     "shell",
        "proto":      "shell",
        "shell":      "shell",
    }.get(language, "shell")


def _derive_env_setup(env_cfg):
    """Derive envSetup shell command from environments.<env>.prepare_env.

    Renders the action to a flat bash command string (script + args).
    Returns None if env has no prepare_env.
    """
    if not env_cfg:
        return None
    prep = env_cfg.get("prepare_env")
    if not isinstance(prep, dict):
        return None
    script = prep.get("script")
    if not script:
        return None
    args = prep.get("args") or []
    parts = [script] + [str(a) for a in args]
    return "bash " + " ".join(parts) if parts else script


def _derive_verify_setup(env_cfg, required_roles):
    """Derive verifySetup probe from environments.<env>.actions.<role>.actions.start.

    Uses the first role's `start` action's `script` (or `actions` cross-role
    `health` if defined). Returns None when no probe can be derived.
    """
    if not env_cfg or not required_roles:
        return None
    cross = (env_cfg.get("actions") or {}).get("health")
    if isinstance(cross, dict) and cross.get("script"):
        script = cross["script"]
        args = cross.get("args") or []
        return "bash " + " ".join([script] + [str(a) for a in args])
    roles = env_cfg.get("roles") or {}
    for role in required_roles:
        role_cfg = roles.get(role) or {}
        acts = (role_cfg.get("actions") or {}).get("start")
        if isinstance(acts, dict) and acts.get("script"):
            return "bash " + acts["script"]  # best-effort; orchestrator can override
    return None


def _chain_test_commands(resolved_cmds):
    """Combine multiple resolved test commands into a single bash sequence.

    Each resolved cmd is {"cmd": str, "timeout_seconds": int}. Output is a
    single shell command that runs them in sequence with && semantics.
    Total timeout is max(timeout_i) + 30s grace.
    """
    if not resolved_cmds:
        return None
    if len(resolved_cmds) == 1:
        return resolved_cmds[0]
    total_timeout = max(c["timeout_seconds"] for c in resolved_cmds) + 30
    parts = [c["cmd"] for c in resolved_cmds]
    return {
        "cmd": " && ".join(parts),
        "timeout_seconds": total_timeout,
    }


def _resolve_track_lint(config, track_cfg):
    """Resolve tracks.<t>.lint override, or fallback to first module's lint.

    Returns dict {"cmd", "timeout_seconds"} or None if neither defined.
    """
    override = track_cfg.get("lint")
    if isinstance(override, dict) and override.get("cmd"):
        timeout = override.get("timeout_seconds")
        return {
            "cmd": override["cmd"],
            "timeout_seconds": timeout if timeout is not None else 1800,
        }
    if isinstance(override, str):
        return {"cmd": override, "timeout_seconds": 1800}
    modules = track_cfg.get("modules") or []
    for m in modules:
        resolved = resolve_module_command(
            config.get("modules") or {}, m, "lint")
        if resolved:
            return resolved
    return None


def cmd_pg_verify_and_merge(args, config, project_root):
    """Build the full context for pg-verify-and-merge.

    Returns a JSON-serializable dict with these top-level keys:
      - tracks:        { track_id: { lint_cmd, modules, is_simple? } }
      - regressionSuites: { suite_name: { module, test_keys, envSetup,
                                          verifySetup, runAllCommand,
                                          outputFormat } }   (only for affected tracks)
      - verify_merge:  { skip_tests_if_no_conflict: bool }
      - flyway:        { migration_path: str }
      - git:           { default_branch: str }
      - __meta:        { affected_tracks_source: 'cli'|'tasks_md'|'git_diff'|'suite_keys' }

    Args:
        args: parsed CLI args list (we accept --affected-tracks and
            --change-dir here).
        config: full .pg/project.yaml dict.
        project_root: absolute path to repo root.
    """
    explicit = None
    change_dir = os.path.join(project_root, ".pg", "changes")
    i = 0
    while i < len(args):
        if args[i] == "--affected-tracks" and i + 1 < len(args):
            explicit = args[i + 1]
            i += 2
        elif args[i] == "--change-dir" and i + 1 < len(args):
            change_dir = args[i + 1]
            i += 2
        else:
            i += 1

    default_branch = (config.get("git") or {}).get("default_branch", "master")

    # Decide AffectedTracks and its provenance
    explicit_given = bool(explicit)
    affected = _infer_affected_tracks(config, change_dir, default_branch,
                                      explicit=explicit)
    if explicit_given:
        source = "cli"
    elif _parse_manifest_track_ids(os.path.join(change_dir, "execution-manifest.yaml")):
        source = "manifest"
    elif _parse_tasks_md_track_ids(os.path.join(change_dir, "tasks.md")):
        source = "tasks_md"
    elif _git_diff_names(default_branch) is not None:
        source = "git_diff"
    else:
        source = "suite_keys"

    # 1. tracks 段
    tracks_out = {}
    all_tracks = config.get("tracks") or {}
    for t_name in affected:
        t_cfg = all_tracks.get(t_name) or {}
        tracks_out[t_name] = {
            "lint_cmd": _resolve_track_lint(config, t_cfg),
            "modules": t_cfg.get("modules") or [],
        }
        if _is_simple_track(config, t_name):
            tracks_out[t_name]["is_simple"] = True

    # 2. regressionSuites 段 (只覆盖 affected ∩ regression.suite)
    modules_cfg = config.get("modules") or {}
    environments_cfg = config.get("environments") or {}
    regression_suites = (config.get("regression") or {}).get("suite") or {}
    suites_out = {}
    for t_name in affected:
        suite_cfg = regression_suites.get(t_name)
        if not suite_cfg:
            continue
        env_name = (suite_cfg.get("environment") or {}).get("name")
        env_cfg = environments_cfg.get(env_name) or {}
        required_roles = (suite_cfg.get("environment") or {}).get("required_roles") or []
        suite_module = suite_cfg.get("module")
        module_cfg = modules_cfg.get(suite_module) or {}

        resolved_test_cmds = []
        output_formats = set()
        explicit_output_format = suite_cfg.get("output_format")
        for tk in (suite_cfg.get("test_keys") or []):
            cmd = resolve_module_command(modules_cfg, suite_module, "test", test_key=tk)
            if cmd:
                resolved_test_cmds.append(cmd)
            output_formats.add(
                explicit_output_format
                or _infer_output_format(module_cfg, tk)
            )

        run_all = _chain_test_commands(resolved_test_cmds)
        # outputFormat: single string if 1 unique value, else sorted list.
        formats_list = sorted(output_formats)
        output_format = (formats_list[0] if len(formats_list) == 1
                         else formats_list)
        suites_out[t_name] = {
            "module":           suite_module,
            "test_keys":        suite_cfg.get("test_keys") or [],
            "envSetup":         _derive_env_setup(env_cfg),
            "verifySetup":      _derive_verify_setup(env_cfg, required_roles),
            "runAllCommand":    run_all,
            "outputFormat":     output_format,
        }

    return {
        "tracks":           tracks_out,
        "regressionSuites": suites_out,
        "verify_merge": {
            "skip_tests_if_no_conflict": (config.get("verify_merge") or {}).get(
                "skip_tests_if_no_conflict", True),
        },
        "flyway":  config.get("flyway")  or {},
        "git":     config.get("git")     or {},
        "__meta": {
            "hostname":               __import__("socket").gethostname(),
            "change_dir":             os.path.relpath(change_dir, project_root),
            "affected_tracks":        affected,
            "affected_tracks_source": source,
            "excluded_simple_tracks": [
                t for t in (config.get("tracks") or {}).keys()
                if _is_simple_track(config, t)
            ],
        },
    }


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
        # Special handling: pg-verify-and-merge builds a derived context
        # (tracks / regressionSuites / etc.) — not a flat config filter.
        if args[0] == "pg-verify-and-merge":
            project_root = os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))  # .opencode/scripts/..
            # Walk up to find project root (where .pg/project.yaml or .pg/project.yaml lives)
            probe = os.path.dirname(os.path.abspath(__file__))
            for _ in range(6):
                if os.path.isfile(os.path.join(probe, ".pg", "project.yaml")):
                    project_root = probe
                    break
                if os.path.isfile(os.path.join(probe, "pg-spec-deprecated", "config.yaml")):
                    project_root = probe
                    break
                if os.path.isfile(os.path.join(probe, "pg-spec", "config.yaml")):
                    project_root = probe
                    break
                probe = os.path.dirname(probe)
            sub_args = args[1:]
            result = cmd_pg_verify_and_merge(sub_args, data, project_root)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            emit_cwd_policy_notice(json_only=json_only)
            _run_validation(data)
            return

        sub_args = args[1:]
        filtered = filter_by_workflow(data, args[0])

        if args[0] == "pg-regression":
            suite_name = None
            i = 0
            while i < len(sub_args):
                if sub_args[i] == "--suite" and i + 1 < len(sub_args):
                    suite_name = sub_args[i + 1]
                    break
                i += 1
            if suite_name:
                filtered = _filter_regression_by_suite(data, filtered, suite_name)

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

    Track→testSuite mapping warnings are non-blocking (stderr only, no
    exit): some tracks (e.g. proto-compile, openapi-gen) intentionally
    have no matching testSuite.
    """
    # Non-blocking warnings first so they always surface.
    suite_warnings = validate_track_suite_mapping(data)
    if suite_warnings:
        print("VALIDATION WARNING: tracks without matching testSuites "
              "(pg-verify-and-merge will skip tests for these):",
              file=sys.stderr)
        for w in suite_warnings:
            print(f"  - track={w['track']}: {w['reason']}", file=sys.stderr)

    errors = validate_scripts(data)
    errors.extend(validate_regression(data))
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
            "points to an existing script and `regression.suite` is valid, "
            "then re-run.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
