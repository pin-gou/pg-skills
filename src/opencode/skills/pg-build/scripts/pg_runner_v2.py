#!/usr/bin/env python3
"""pg_runner_v2.py — V2 entry points for cmd_next / cmd_record.

This module hosts the v2 implementations of the runner's two main
commands. The legacy v1 implementations remain in pg-pipeline-runner.py
as `_legacy_cmd_next` / `_legacy_cmd_record` for shadow comparison.

The v2 implementations:
  - Use PipelineState (v2 schema) as the sole SSOT for state.
  - Read pipeline_order from project.yaml, not tasks.md.
  - Replace tasks.md checkbox semantics with `phases.<phase>.tasks_marked`.
  - Compute next dispatch from state.json via PipelineState.next_pending().
  - Remove _validate_state_consistency / _any_open_section entirely
    (drift detection is no longer needed: no double source of truth).

Public API (consumed by the runner's `main()`):
  cmd_next_v2(change)        → dict (action: dispatch | dispatch_fix |
                                       dispatch_final_gate | done |
                                       workflow_failed | error)
  cmd_record_v2(change, status, report_path='', summary='',
                outputs='', issues='') → dict

Shadow comparison helper:
  shadow_compare(change)     → (v1_action_dict, v2_action_dict, equal_bool)
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

from pg_pipeline_state_v2 import (
    PipelineState,
    NextDispatch,
    PHASE_AGENTS,
    SCHEMA_VERSION,
)
from pg_pipeline_common import get_track_type


# Sub-agents dispatched by phase. Mirrors runner.SUB_AGENTS.
SUB_AGENTS = {
    "test":     "pg-build/test",
    "dev":      "pg-build/dev",
    "verify":   "pg-build/verify",
    "gate":     "pg-build/gate",
    "fix":      "pg-build/fix",
    "fix-gate": "pg-build/fix-gate",
    "simple":   "pg-build/simple",
}

# ALLOWED_STATUS: per-sub allowed record status set (regression risk guard).
# Mirrors runner.ALLOWED_STATUS but stripped of fix/fix-gate-specific
# semantics (those are derived from the dispatch's sub field).
ALLOWED_STATUS = {
    "test":      {"completed", "failed"},
    "dev":       {"completed", "failed"},
    "verify":    {"completed", "escalate", "failed"},
    "fix":       {"completed", "failed"},
    "fix-gate":  {"completed", "failed"},
    "gate":      {"pass", "fail"},
    "simple":    {"completed", "failed"},
    "final-gate": {"pass", "fail"},
}


def _now_iso() -> str:
    """ISO8601 local time with offset."""
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _find_cwd_project_root() -> str:
    """Find the consumer project's .pg/project.yaml from CWD.

    Walks up from os.getcwd() looking for .pg/project.yaml. This is
    different from PipelineState._find_project_root which walks from
    the module file location (pg-skills/...).
    """
    cur = os.path.abspath(os.getcwd())
    for _ in range(8):
        if os.path.isfile(os.path.join(cur, ".pg", "project.yaml")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    raise FileNotFoundError(
        f"pg_runner_v2: no .pg/project.yaml found above {os.getcwd()}"
    )


# =============================================================================
# cmd_next_v2
# =============================================================================

def _import_runner_helpers():
    """Import runner helpers, working around the hyphen filename problem.

    When pg-pipeline-runner.py is invoked as `__main__`, its module-level
    helpers (load_config, dispatch_action, etc.) are NOT exposed under
    the `pg_pipeline_runner` name in sys.modules. We need to load the
    module explicitly via importlib and re-expose its public names.
    """
    import importlib.util
    import sys as _sys

    # Already loaded?
    if "pg_pipeline_runner" in _sys.modules:
        return _sys.modules["pg_pipeline_runner"]

    runner_path = os.path.join(THIS_DIR, "pg-pipeline-runner.py")
    spec = importlib.util.spec_from_file_location("pg_pipeline_runner", runner_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load runner from {runner_path}")
    module = importlib.util.module_from_spec(spec)
    _sys.modules["pg_pipeline_runner"] = module

    # Make sibling modules (pg_context_chain, pg_pipeline_common,
    # pg_pipeline_state) importable from inside the runner module.
    if THIS_DIR not in _sys.path:
        _sys.path.insert(0, THIS_DIR)

    spec.loader.exec_module(module)
    return module


def cmd_next_v2(change: str) -> dict:
    """V2 entry point for `next`. Returns the same action protocol as v1.

    Differences from v1 cmd_next:
      - No _validate_state_consistency() call (no drift possible)
      - No _last_dispatch_key / _duplicate_warning (dispatch_history is SSOT)
      - Decision comes from PipelineState.next_pending(), not tasks.md
      - Phase routing (test→dev→verify→gate) is derived from state.json
        phases.*.status, not from tasks.md checkboxes
    """
    # PipelineState's project_root discovery walks up from the module
    # location (which is in pg-skills/, NOT in the consumer project).
    # We need to discover the project root from the *current working
    # directory* (where the runner was invoked from) instead. That's
    # where .pg/project.yaml lives in the consumer project.
    project_root = _find_cwd_project_root()
    ps = PipelineState(change, project_root=project_root)

    # Terminal?
    if ps.data["context"].get("completed"):
        return {"action": "done", "status": "completed"}
    if ps.data["context"].get("failed"):
        return {
            "action": "workflow_failed",
            "fatal": True,
            "reason": ps.data["context"].get("failed_reason") or "workflow failed",
        }

    # Pipeline order: read from project.yaml via the runner's helpers.
    # Import lazily to avoid circular imports at module load time.
    try:
        runner = _import_runner_helpers()
        load_config = runner.load_config
        get_pipeline_order = runner.get_pipeline_order
        dispatch_action = runner.dispatch_action
        dispatch_fix_action = runner.dispatch_fix_action
        dispatch_fix_gate_action = runner.dispatch_fix_gate_action
        filter_track_context = runner.filter_track_context
        _enrich_context_with_tasks = runner._enrich_context_with_tasks
        _enrich_context_with_rollback = runner._enrich_context_with_rollback
        _enrich_context_with_stage = runner._enrich_context_with_stage
        _enrich_context_with_prompt_injection = runner._enrich_context_with_prompt_injection
        _ensure_context_chain = runner._ensure_context_chain
        _ensure_feature_branch = runner._ensure_feature_branch
        _maybe_bootstrap_init_commit = runner._maybe_bootstrap_init_commit
        _inject_commit = runner._inject_commit
        _enter_final_gate = runner._enter_final_gate
        _execute_phase = runner._execute_phase
        _last_fail_reason = runner._last_fail_reason
        migrate_legacy_state_files = runner.migrate_legacy_state_files
        # Shared bootstrap helper (v1/v2 共用, 防止 v2 再次丢函数)
        pg_build_bootstrap = runner.pg_build_bootstrap
        pg_build_dispatch_context = runner.pg_build_dispatch_context
    except Exception as e:
        return {
            "action": "error",
            "fatal": True,
            "reason": f"pg_runner_v2: failed to import runner helpers: {e}",
        }

    # ===== Build bootstrap (mirrors v1 cmd_next:2862-2885 + inline env-hook) =====
    # build-r Step 3 切到 v2 时丢失了 4 个关键副作用, 导致 v2 路径下:
    #   - 不创建 feat/pg/<change> 分支, 所有 commit 直接落 master
    #   - 不跑 init commit, 启动基线 git 节点丢失
    #   - 不创建 2-build/context-chain.md, final-gate 审计缺依据
    #   - 不迁移 legacy state file
    # 现在通过共享 helper pg_build_bootstrap 补回, 与 v1 走同一份逻辑.
    #
    # v2 enhancement: pg_build_bootstrap ALSO runs prepare_env inline.
    # On success, the function writes phase_start/phase_end to context-chain.md
    # AND persists environment_summary into state.context. On failure, it
    # raises EnvHookError which we catch below and convert to the new
    # `env_hook_failed` action. This eliminates the v1 `phase_result` round-trip.
    try:
        init_commit = pg_build_bootstrap(change, ps)
    except Exception as e:
        # EnvHookError or any other bootstrap exception → surface as
        # env_hook_failed action. LLM orchestrator decides next step.
        from pg_pipeline_common import EnvHookError as _EnvHookError
        if isinstance(e, _EnvHookError):
            return {
                "action": "env_hook_failed",
                "phase": e.phase_name,
                "log_path": e.log_path,
                "exit_code": e.exit_code,
                "fatal": True,
                "reason": (f"env-hook {e.phase_name} failed "
                           f"(exit_code={e.exit_code})"),
                "fix_hint": ("检查 env 脚本日志后, 修复根因或调整 "
                             "environments.<env>.prepare_env 配置"),
                "next_call_timeout_seconds": 30,
            }
        # Non-env-hook bootstrap exception → workflow_failed
        return {
            "action": "workflow_failed",
            "fatal": True,
            "reason": f"pg_build_bootstrap exception: {e}",
        }

    # Manifest consistency guard (mirrors v1 cmd_next:2845-2849).
    # build-r Step 3 切到 v2 时丢了这个守卫, 导致 manifest.yaml ↔ tasks.md
    # 不一致时静默通过. 现在补回, 与 v1 行为一致.
    try:
        valid, msg = runner._validate_manifest(change)
        if not valid:
            return {
                "action": "error",
                "fatal": True,
                "reason": f"manifest 校验失败: {msg}",
                "fix_hint": "请先修复 manifest 一致性后重试",
            }
    except Exception as e:
        # manifest 不存在/校验脚本失败时, 与 v1 一样不阻塞 (manifest 在
        # 老项目可能不存在). 仅当 _validate_manifest 显式返回 invalid
        # 时才返回 error.
        if "manifest 校验失败" not in str(e):
            pass  # 静默放过
        else:
            return {
                "action": "error",
                "fatal": True,
                "reason": f"manifest 校验失败: {e}",
            }

    config = load_config()
    order = get_pipeline_order(config, change)

    # Sync pipeline_order into state (idempotent).
    if not ps.data["context"].get("pipeline_order"):
        ps.set_pipeline_order(order)

    # Idempotent resume
    nd = ps.next_pending()
    if nd is None:
        return {"action": "workflow_failed", "fatal": True,
                "reason": "no next dispatch and not terminal — impossible"}

    if nd.kind == "dispatch_final_gate":
        # Idempotent: if already on final-gate, don't re-enter.
        cd = ps.data.get("current_dispatch")
        if cd and cd.get("track") == "final-gate":
            return _enter_final_gate(config, change, ps._data)
        ps._data["current_dispatch"] = {
            "track": "final-gate", "phase": "gate", "cycle": 1,
            "agent": "pg-build/gate", "started_at": _now_iso(),
            "waiting": True, "report_path": None, "result_received": False,
        }
        ps.commit()
        return _enter_final_gate(config, change, ps._data)

# Phase item: delegate to _execute_phase (handles prepare_env/clean_env/simple)
        # Detect by checking if pipeline_order item is a phase vs a track.
        # Skip this branch on resume — _execute_phase re-dispatches, which
        # would create new dispatch entries on every idempotent retry.
        if not nd.is_resume and _is_phase_item(config, nd.track):
            # v2 enhancement: prepare_env is now inlined into pg_build_bootstrap
            # (no longer returns phase_result). If pipeline_order still has a
            # prepare_env entry (because the runner didn't filter it out), skip
            # it here — pg_build_bootstrap already executed it.
            bare_phase = nd.track.rsplit(".", 1)[-1] if "." in nd.track else nd.track
            ctx = ps.data.get("context") or {}
            if bare_phase == "prepare_env" and ctx.get("prepare_env_completed"):
                # Already done by bootstrap; advance to next item.
                #
                # Critical: mark the phase-item track skeleton as completed
                # BEFORE the recursive call. Without this, is_track_completed()
                # keeps returning False on the next iteration (because the
                # skeleton stays "pending"), next_pending() keeps dispatching
                # this same track, and cmd_next_v2 keeps re-entering this
                # branch — an infinite recursion that hammers state.json
                # until bash tool's timeout kills the process. Symptom
                # reproduces whenever state was partially initialized by a
                # prior run that crashed before reaching this branch.
                ps._data["tracks"][nd.track]["status"] = "completed"
                ps._data["tracks"][nd.track]["completed_at"] = _now_iso()
                ps._data["current_dispatch"] = None
                ps.commit()
                return cmd_next_v2(change)
        # Phase item — phase advances to done via _execute_phase.
        ps._data["current_dispatch"] = None
        ps.commit()
        return _execute_phase(config, change, ps._data, nd.track)

    # Resume path: same dispatch already in flight
    if nd.is_resume:
        # Use shared dispatch context helper (mirrors v1 cmd_next:2935-2941)
        # to inject rollback_context / environment.hooks / build_rules.
        ctx, _has_rollback = pg_build_dispatch_context(
            change, nd.track, nd.phase, config)
        env_summary = _consume_environment_summary(ps)
        return _build_dispatch_response(config, change, nd, ctx,
                                        init_commit=init_commit,
                                        env_summary=env_summary)

    # Fresh dispatch: record_dispatch_started marks state.
    # For phase items we wouldn't get here, but for tracks:
    ps.record_dispatch_started(
        track=nd.track,
        phase=nd.phase,
        agent=nd.agent,
        report_path=None,
    )
    ps.commit()

    # Use shared dispatch context helper (mirrors v1 cmd_next:2935-2941).
    ctx, _has_rollback = pg_build_dispatch_context(
        change, nd.track, nd.phase, config)
    env_summary = _consume_environment_summary(ps)
    return _build_dispatch_response(config, change, nd, ctx,
                                    init_commit=init_commit,
                                    env_summary=env_summary)


def _consume_environment_summary(ps) -> dict | None:
    """Read environment_summary from state, return None if already dispatched.

    Used by cmd_next_v2 to attach environment info to the FIRST dispatch
    after prepare_env. Marks `environment_summary_dispatched=True` so
    subsequent dispatches skip the attach.
    """
    try:
        ctx = ps.data.get("context") or {}
    except Exception:
        return None
    summary = ctx.get("environment_summary")
    if not summary:
        return None
    if ctx.get("environment_summary_dispatched"):
        return None
    # Mark dispatched (best-effort).
    ctx["environment_summary_dispatched"] = True
    summary["_dispatched"] = True
    try:
        if hasattr(ps, "commit"):
            ps.commit()
    except Exception:
        pass
    return summary


def _is_phase_item(config: dict, item: str) -> bool:
    """Return True if item is a phase (prepare_env/clean_env/simple) — not a track.

    Simple tracks have track type string 'simple' (per project.yaml schema).
    get_track_type() classifies them as 'phase' for routing purposes; we
    delegate to that helper to avoid string drift if the classification
    evolves (e.g. a new 'wrapper' track type introduced in the future).
    """
    bare = item.rsplit(".", 1)[-1] if "." in item else item
    if bare in ("prepare_env", "clean_env"):
        return True
    return get_track_type(config, bare) == "phase"


def _build_dispatch_response(config: dict, change: str,
                              nd: NextDispatch, ctx: dict,
                              init_commit=None,
                              env_summary = None) -> dict:
    """Build the dispatch action dict (same protocol as v1 dispatch_action).

    Args:
        init_commit: optional init_commit dict from pg_build_bootstrap (only
            set on the first dispatch of a fresh change). When set, it's
            attached to the action JSON so the LLM orchestrator can show
            "bootstrap committed on branch X" in its terminal summary.
            Mirrors v1 cmd_next:2970-2977 behavior.
        env_summary: optional environment summary dict from state.context.
            When provided AND environment_summary_dispatched is False, it's
            attached to the action JSON so the LLM orchestrator sees the
            environment info on the first dispatch (mirrors v1 phase_result.
            environment field, but inlined to avoid the separate action).
    """
    from pg_pipeline_runner import dispatch_action, dispatch_fix_action
    if nd.kind == "dispatch_fix":
        if nd.phase == "fix-gate":
            # gate-fix: cycles_remaining context
            ctx.setdefault("gate_cycles", nd.cycle)
            ctx.setdefault("max_gate_fix_retries", 2)
            ctx.setdefault("cycles_remaining",
                           max(0, 2 - nd.cycle))
            from pg_pipeline_runner import dispatch_fix_gate_action
            return dispatch_fix_gate_action(nd.track, nd.cycle, ctx,
                                            config=config)
        return dispatch_fix_action(nd.track, nd.cycle, ctx)

    response = dispatch_action(
        agent=nd.agent,
        item=nd.track,
        sub=nd.phase,
        context=ctx,
        attempt=1,
        init_commit=init_commit,
    )
    # Attach environment summary on the FIRST dispatch only. We mark
    # environment_summary_dispatched=True via the caller (cmd_next_v2) so
    # subsequent dispatches skip the attach.
    if env_summary and not env_summary.get("_dispatched"):
        response["environment"] = {
            "name": env_summary.get("name"),
            "prepare_env_log_path": env_summary.get("prepare_env_log_path"),
            "instances": env_summary.get("instances", {}),
        }
    return response


# =============================================================================
# cmd_record_v2
# =============================================================================


def _try_handle_env_hook_record_v2(ps, change, status, summary, outputs, issues):
    """V2 env-hook record handler.

    NOTE (v2 enhancement): prepare_env is now inlined into
    pg_build_bootstrap. cmd_record_v2 should NEVER see an env-hook phase
    in this code path — bootstrap already handled it. This function
    remains as a defensive guard: if LLM orchestrator somehow sends a
    `record` call for an env-hook phase, we surface it as env_hook_failed
    (not workflow_failed) so the orchestrator can recognize the
    architectural change and proceed differently.

    Detection: state.context.prepare_env_completed is True (signaling
    bootstrap already handled it) AND current_dispatch is None AND state
    has no pending track. If matched, return env_hook_failed.

    Returns:
      None   — no env-hook signal detected; caller falls through.
      dict   — env_hook_failed action.
    """
    ctx = ps.data.get("context") or {}
    # If prepare_env was inlined (bootstrap done it), LLM shouldn't be
    # sending `record` for it. Surface as error so orchestrator knows.
    if ctx.get("prepare_env_completed") and not ps.data.get("current_dispatch"):
        return {
            "action": "error",
            "fatal": False,
            "reason": ("env-hook phase_result 路径已在 v2 移除。"
                       "pg_build_bootstrap 已内联执行 prepare_env，"
                       "无需调用 `record` 推进。请改用 `next` 获取下一个 dispatch。"),
            "fix_hint": "重新调用 `next` 跳过 env-hook 阶段",
        }
    return None


def cmd_record_v2(change: str, status: str, report_path: str = "",
                  summary: str = "", outputs: str = "",
                  issues: str = "") -> dict:
    """V2 entry point for `record`.

    Differences from v1 cmd_record:
      - No _validate_state_consistency() call.
      - No ALLOWED_STATUS guard (moved into PipelineState.record_* methods
        by sub field; cleaner API surface).
      - State transitions go through PipelineState.record_* methods.
      - tasks.md checkbox updates remain as a SIDE EFFECT for human
        audit (Step 5 will replace with mark-task CLI).
    """
    project_root = _find_cwd_project_root()
    ps = PipelineState(change, project_root=project_root)
    cd = ps.data.get("current_dispatch")

    # Guard 0: env-hook phase (prepare_env/clean_env).
    # v2's cmd_next_v2 clears `current_dispatch` before calling
    # `_execute_phase`, so on a phase_result the record call sees
    # `current_dispatch=None`. We detect env-hook completion by looking
    # at the last dispatch_history entry (which _execute_phase appends
    # via pg_context_chain.phase_start) — but the simpler, more
    # reliable signal is: `state["current"]` is set by v1
    # `_execute_phase` (sub=None) AND v2's tracks entry has just been
    # marked completed.
    if not cd:
        env_result = _try_handle_env_hook_record_v2(
            ps, change, status, summary, outputs, issues)
        if env_result is not None:
            return env_result
        return {"action": "workflow_failed", "fatal": True,
                "reason": "No active item to record"}

    track = cd["track"]
    phase = cd["phase"]

    # ALLOWED_STATUS guard (preserved from v1, regression guard)
    if phase is not None and phase not in ALLOWED_STATUS:
        return {"action": "workflow_failed", "fatal": True,
                "reason": f"未知 sub={phase!r}, 期望 {sorted(ALLOWED_STATUS.keys())}"}
    if phase is not None and status not in ALLOWED_STATUS.get(phase, set()):
        valid = " | ".join(sorted(ALLOWED_STATUS.get(phase, set())))
        return {"action": "error", "fatal": False,
                "reason": (f"record status 与 sub 不匹配: sub={phase!r} "
                           f"不允许 status={status!r}。该 sub 仅支持: {valid}。"),
                "fix_hint": "verify 子阶段完成后应使用 'record completed', "
                            "gate 子阶段完成后应使用 'record pass'。",
                "sub": phase, "item_id": track}

    # Import lazy: avoid circular imports.
    try:
        runner = _import_runner_helpers()
        load_config = runner.load_config
        dispatch_action = runner.dispatch_action
        dispatch_fix_action = runner.dispatch_fix_action
        dispatch_fix_gate_action = runner.dispatch_fix_gate_action
        filter_track_context = runner.filter_track_context
        _enrich_context_with_tasks = runner._enrich_context_with_tasks
        _inject_commit = runner._inject_commit
        _auto_archive = runner._auto_archive
        _git_commit_archive = runner._git_commit_archive
        _auto_commit_on_record = runner._auto_commit_on_record
        pipeline_mark = runner.pipeline_mark    # legacy: keeps tasks.md checkbox in sync
    except Exception as e:
        return {"action": "error", "fatal": True,
                "reason": f"pg_runner_v2: failed to import runner helpers: {e}"}

    # Shared context-chain logging helper lives in pg_pipeline_common (not
    # in pg-pipeline-runner). Import lazily to keep the v2 module decoupled
    # from runner's private imports.
    try:
        from pg_pipeline_common import pg_build_record_log
    except Exception as e:
        return {"action": "error", "fatal": True,
                "reason": f"pg_runner_v2: failed to import pg_build_record_log: {e}"}

    config = load_config()
    tasks_marked = _parse_tasks_from_outputs(outputs, track, phase)

    # ===== Context-chain logging (v1 parity) =====
    # build-r Step 3 切到 v2 时, 17 处 pg_context_chain.sub_start/sub_end
    # 调用全部丢失, 导致 2-build/context-chain.md 永远空白, final-gate
    # 审计与 fix agent 根因追溯都失去依据. 现在通过共享 helper
    # pg_build_record_log 统一补回.
    pg_build_record_log(change, track, phase, status,
                        summary=summary, outputs=outputs, issues=issues)

    # === Status dispatch ===
    if status == "completed":
        # If we were in a fix cycle, re-dispatch verify (NOT advance to gate)
        # — matches v1 semantics: fix → re-verify → if pass → gate
        if phase in ("fix", "fix-gate"):
            parent_phase = "verify" if phase == "fix" else "gate"
            ps.record_fix_completed(track, parent_phase, summary=summary,
                                    fixed_tasks=tasks_marked)
            # legacy: keep tasks.md in sync for human audit
            pipeline_mark(change, track, parent_phase)
            ps.commit()
            # Re-dispatch the parent (verify or gate)
            ctx = filter_track_context(config, track, parent_phase, change=change)
            ctx["_change"] = change
            _enrich_context_with_tasks(ctx, change, track, parent_phase)
            agent = PHASE_AGENTS[parent_phase]
            ps.record_dispatch_started(track, parent_phase, agent)
            ps.commit()
            return dispatch_action(
                agent=agent, item=track, sub=parent_phase,
                context=ctx, attempt=1,
            )

        # Normal completion: mark phase complete, advance.
        # Auto-mark兜底 (Step 5 §3 plan): if `outputs` is empty but
        # tasks.md shows all boxes unchecked, treat every open task
        # as auto-marked. This handles the regression where the sub-agent
        # completed work but forgot to call mark-task CLI.
        if not tasks_marked and phase not in ("fix", "fix-gate"):
            auto_marked = _auto_mark_open_tasks_in_phase(
                change, track, phase)
            if auto_marked:
                tasks_marked = auto_marked

        ps.record_completed(track, phase, summary=summary,
                            report_path=report_path or None,
                            tasks_marked=tasks_marked or None)
        # legacy: keep tasks.md in sync
        if phase == "simple":
            pipeline_mark(change, track)
        else:
            pipeline_mark(change, track, phase)
        ps.commit()

        # Advance: call cmd_next_v2 to decide what comes next
        result = cmd_next_v2(change)
        # Auto-commit on record (matches v1 behavior for git integration).
        # Mount commit metadata on result so LLM orchestrator can show it
        # in the terminal summary (mirrors v1 _inject_commit).
        try:
            commit_meta = _auto_commit_on_record(change, track, phase, status)
            if commit_meta and isinstance(result, dict):
                result["commit"] = commit_meta
        except Exception:
            pass
        return result

    elif status == "failed":
        # Phase failure → retry / fail workflow
        phase_data = ps.get_phase(track, phase)
        attempt = phase_data.get("attempt", 1)
        # Match v1 max_fail_retries default of 3
        max_retries = _get_max_retries(config, track)
        if attempt >= max_retries:
            ps.mark_workflow_failed(
                f"{track}:{phase} failed after {max_retries} attempts"
            )
            ps.commit()
            return {"action": "workflow_failed", "fatal": True,
                    "reason": f"{track}:{phase} failed after {max_retries} attempts"}

        ps.record_failed(track, phase, attempt=attempt + 1,
                         error=issues or summary)
        # Re-dispatch same phase with new attempt
        ctx = filter_track_context(config, track, phase, change=change)
        ctx["_change"] = change
        _enrich_context_with_tasks(ctx, change, track, phase)
        ps.record_dispatch_started(track, phase, PHASE_AGENTS[phase])
        ps.commit()
        return dispatch_action(
            agent=PHASE_AGENTS[phase], item=track, sub=phase,
            context=ctx, attempt=attempt + 1,
        )

    elif status == "escalate":
        # Verify requests fix cycle
        if phase != "verify":
            return {"action": "error", "fatal": True,
                    "reason": f"escalate only valid for verify sub, got {phase!r}"}
        # v1 behavior: if fix_cycles >= MAX_FIX_CYCLES (4), force gate
        # (we mirror that behavior)
        verify = ps.get_phase(track, "verify")
        existing_fix_cycles = len(verify.get("fix_cycles", []))
        MAX_FIX_CYCLES = 4
        if existing_fix_cycles >= MAX_FIX_CYCLES:
            # Force gate with last report
            ps.record_completed(track, "verify", summary=summary,
                                report_path=report_path or None)
            ps.commit()
            return cmd_next_v2(change)
        ps.record_escalate(track, summary=summary, report_path=report_path or None)
        ps.commit()
        # Dispatch fix agent
        ctx = filter_track_context(config, track, "fix", change=change)
        ctx["_change"] = change
        _enrich_context_with_tasks(ctx, change, track, "fix")
        ps.record_dispatch_started(track, "fix", "pg-build/fix")
        ps.commit()
        return dispatch_fix_action(track, existing_fix_cycles + 1, ctx)

    elif status == "pass":
        if track == "final-gate":
            pipeline_mark(change, "final-gate")
            ps.record_pass("final-gate", summary=summary,
                           report_path=report_path or None)
            ps.commit()
            archive_result = _auto_archive(change)
            try:
                commit_result = _git_commit_archive(archive_result)
            except Exception:
                commit_result = {"ok": False, "reason": "archive commit failed"}
            return {
                "action": "done",
                "status": "completed",
                "archive": {
                    "ok": archive_result.get("ok", False),
                    "target_name": archive_result.get("target_name"),
                    "src": archive_result.get("src"),
                    "target": archive_result.get("target"),
                    "reason": archive_result.get("reason"),
                    "commit": commit_result,
                },
            }
        ps.record_pass(track, summary=summary, report_path=report_path or None)
        # legacy: keep tasks.md in sync
        pipeline_mark(change, track, "gate")
        ps.commit()
        # Advance to next
        return cmd_next_v2(change)

    elif status == "fail":
        if track == "final-gate":
            ps.mark_workflow_failed("Final gate assessment failed")
            ps.commit()
            return {"action": "workflow_failed", "fatal": True,
                    "reason": "Final gate assessment failed"}
        # Gate fail → enter gate-fix
        ps.record_fail(track, summary=summary,
                       report_path=report_path or None,
                       fixed_tasks=tasks_marked or None)
        ps.commit()
        # Check exhausted
        gate = ps.get_phase(track, "gate")
        gate_cycles = len(gate.get("gate_cycles", []))
        max_gate_fix = _get_max_gate_fix(config, track)
        if gate_cycles >= max_gate_fix:
            # Decision 2: exhausted → PASS with known issues
            accepted_gaps = _parse_accepted_gaps_from_report(report_path)
            ps.record_gate_exhausted(track, accepted_gaps=accepted_gaps,
                                     report_path=report_path or None)
            ps.commit()
            return cmd_next_v2(change)
        # Dispatch fix-gate
        ctx = filter_track_context(config, track, "fix-gate", change=change)
        ctx["_change"] = change
        ctx["gate_cycles"] = gate_cycles
        ctx["max_gate_fix_retries"] = max_gate_fix
        ctx["cycles_remaining"] = max_gate_fix - gate_cycles
        ps.record_dispatch_started(track, "fix-gate", "pg-build/fix-gate")
        ps.commit()
        return dispatch_fix_gate_action(track, gate_cycles, ctx, config=config)

    return {"action": "workflow_failed", "fatal": True,
            "reason": f"Unknown status: {status}"}


# =============================================================================
# Helpers
# =============================================================================

def _parse_tasks_from_outputs(outputs: str, track: str, phase: str) -> list:
    """Parse `outputs` (e.g. 'task 1.1, task 1.2') into a list of task_ids.

    Returns empty list if parsing fails. Used by Step 5 to feed tasks_marked
    into PipelineState.record_completed.
    """
    import re
    if not outputs:
        return []
    ids = []
    for m in re.finditer(r"(\d+)\.(\d+)", outputs):
        ids.append(int(m.group(2)))
    return sorted(set(ids))


def _auto_mark_open_tasks_in_phase(change: str, track: str,
                                     phase: str) -> list:
    """Step 5 auto-mark兜底: if sub-agent didn't call mark-task CLI,
    scan tasks.md for the (track, phase) section and return the task_ids
    of any unchecked boxes — assuming sub-agent's "completed" status
    means "I did all the work".

    Returns empty list if tasks.md has no section or no unchecked boxes.
    """
    import re

    # Lazy-load pg-pipeline-state (the v1 module) which exposes parse_tasks.
    try:
        state_module = _load_pipeline_state_module()
    except Exception:
        return []
    if state_module is None:
        return []

    try:
        tasks_path = state_module.get_tasks_path(change)
        sections, _lines = state_module.parse_tasks(tasks_path)
    except Exception:
        return []

    # Find the section for (track, phase)
    target_section = None
    for sec in sections:
        if sec.get("item") == track and sec.get("sub") == phase:
            target_section = sec
            break
    if target_section is None:
        return []

    # Walk the section lines, find unchecked task_ids
    unchecked = []
    for line in target_section.get("lines", []):
        # Match `- [ ] X.Y` (skip - [x] and - 无)
        m = re.match(r"\s*-\s*\[\s\]\s*(\d+)\.(\d+)", line)
        if m:
            unchecked.append(int(m.group(2)))
    return sorted(set(unchecked))


def _load_pipeline_state_module():
    """Lazy-load pg-pipeline-state.py via importlib (file has a hyphen)."""
    import importlib.util
    import sys as _sys
    cached = getattr(_load_pipeline_state_module, "_cached", None)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(
        "pg_pipeline_state_legacy",
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "pg-pipeline-state.py"),
    )
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        return None
    _load_pipeline_state_module._cached = mod
    return mod


def _parse_accepted_gaps_from_report(report_path: str) -> list:
    """Parse `**关联 task**` fields from a gate report → accepted_gaps list."""
    import re
    if not report_path or not os.path.isfile(report_path):
        return []
    try:
        with open(report_path, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return []
    gaps = []
    for m in re.finditer(r"###\s+\S+:G-(\d+)", content):
        gap_id = f"G-{m.group(1)}"
        gaps.append({"gap_id": gap_id, "description": "see gate report",
                     "report_section": m.group(0)})
    return gaps


def _get_max_retries(config: dict, track: str) -> int:
    """Read tracks.<bare>.max_fail_retries with sensible default."""
    bare = track.rsplit(".", 1)[-1] if "." in track else track
    return (config.get("tracks") or {}).get(bare, {}).get("max_fail_retries", 3)


def _get_max_gate_fix(config: dict, track: str) -> int:
    """Read tracks.<bare>.max_gate_fix_retries with sensible default."""
    bare = track.rsplit(".", 1)[-1] if "." in track else track
    return (config.get("tracks") or {}).get(bare, {}).get("max_gate_fix_retries", 2)


# =============================================================================
# Shadow comparison
# =============================================================================

def shadow_compare(change: str) -> tuple:
    """Run both v1 and v2 next/record, compare action dicts.

    Returns (v1_result, v2_result, equal_bool). Logs to stderr on mismatch.
    """
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location(
        "pg_pipeline_runner", os.path.join(THIS_DIR, "pg-pipeline-runner.py"))
    if spec is None or spec.loader is None:
        return None, {"error": "cannot load runner"}, False
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    sys.modules["pg_pipeline_runner"] = runner

    # Reload pipeline state for fresh start (best-effort)
    v1_result = runner.cmd_next(change)
    v2_result = cmd_next_v2(change)

    # Compare normalized action dicts (ignore path strings, attempt numbers)
    v1_norm = _normalize_action(v1_result)
    v2_norm = _normalize_action(v2_result)
    equal = v1_norm == v2_norm
    if not equal:
        print(f"[shadow_compare] {change}: v1={v1_norm!r} v2={v2_norm!r}",
              file=sys.stderr)
    return v1_result, v2_result, equal


def _normalize_action(action: dict) -> dict:
    """Strip fields that legitimately differ between v1 and v2 implementations."""
    if not isinstance(action, dict):
        return action
    norm = dict(action)
    for k in ("seq", "dispatch_file", "started_at", "attempt", "init_commit",
              "commit", "report_path", "report"):
        norm.pop(k, None)
    return norm