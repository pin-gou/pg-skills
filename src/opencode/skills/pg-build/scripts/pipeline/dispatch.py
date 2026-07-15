"""Dispatch — 构建 action JSON 与 dispatch_file。"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone, timedelta
from typing import Any

from pipeline.events import PipelineAction
from pipeline.state import PipelineState, TrackState, PhaseState
from pipeline.config import (
    load_project_config,
    resolve_module_roots,
    resolve_module_details,
    resolve_test_commands,
    resolve_env_instances,
    resolve_hooks,
    resolve_build_rules,
)
from pipeline.tasks_md import extract_section_content
from template_engine.renderer import render_dispatch_file


_SHANGHAI = timezone(timedelta(hours=8))
PHASE_AGENTS: dict[str, str] = {
    "test":     "pg-build/test",
    "dev":      "pg-build/dev",
    "verify":   "pg-build/verify",
    "gate":     "pg-build/gate",
    "fix":      "pg-build/fix",
    "fix-gate": "pg-build/fix-gate",
    "simple":   "pg-build/simple",
    # v3.5: scenario track 专用
    "scenario-prepare": "pg-build/scenario-prepare",
    "scenario-execute": "pg-build/scenario-execute",
    "scenario-fix":     "pg-build/scenario-fix",
}
FINAL_GATE_AGENT = "pg-build/gate"

# phase 对应的合法 status 枚举（与 reducer 状态守卫表一致）
PHASE_ALLOWED_STATUSES: dict[str, str] = {
    "test": "completed | failed",
    "dev": "completed | failed",
    "simple": "completed | failed",
    "verify": "completed | escalate | failed",
    "fix": "completed | failed",
    "fix-gate": "completed | failed",
    "gate": "pass | fail",
    # v3.5: scenario track 专用
    "scenario-prepare": "completed | failed",
    "scenario-execute": "completed | escalate | failed",
    "scenario-fix":     "completed | failed",
}

_SEQ_COUNTERS: dict[str, int] = {}
_PROJECT_CONFIG_CACHE: dict[str, Any | None] = {}
_PROJECT_ROOT_CACHE: str = ""


def _set_project_root(root: str) -> None:
    global _PROJECT_ROOT_CACHE
    _PROJECT_ROOT_CACHE = root


def _load_project_config_cached() -> dict[str, Any]:
    """从 project.yaml 读取配置，模块级缓存避免重复 I/O。"""
    if "config" in _PROJECT_CONFIG_CACHE:
        return _PROJECT_CONFIG_CACHE["config"] or {}
    if _PROJECT_ROOT_CACHE:
        cfg = load_project_config(_PROJECT_ROOT_CACHE)
        _PROJECT_CONFIG_CACHE["config"] = cfg
        return cfg or {}
    return {}


def _allocate_seq(change_root: str) -> int:
    """分配下一个全局递增 seq。

    首次调用时扫描 2-build/ 下 NNN-*.md 文件找最大 seq 作为种子，
    后续仅仅递增 in-memory counter。
    """
    build_dir = os.path.join(change_root, "2-build")
    if change_root not in _SEQ_COUNTERS:
        max_seen = 0
        if os.path.isdir(build_dir):
            for fname in os.listdir(build_dir):
                if not fname.endswith(".md"):
                    continue
                m = re.match(r"^(\d{3})-", fname)
                if m:
                    try:
                        max_seen = max(max_seen, int(m.group(1)))
                    except ValueError:
                        continue
        _SEQ_COUNTERS[change_root] = max_seen
    _SEQ_COUNTERS[change_root] += 1
    return _SEQ_COUNTERS[change_root]


def _format_seq(seq: int) -> str:
    """3 位零填充格式化。"""
    return f"{seq:03d}"


def _format_yaml_block(label: str, content: str) -> str:
    """将 YAML 内容包装为 Markdown 代码块 + label。

    输出格式：
      - {label}:
      ```yaml
      {content}
      ```

    content 为空时返回空字符串。
    """
    if not content or not content.strip():
        return ""
    return f"- {label}:\n```yaml\n{content}\n```"


def extract_design_verification_criteria(change_root: str, track: str) -> str:
    """从 design.md 提取当前 track 对应的 Verification Criteria 节内容。

    搜索模式: "### <stage> <bare> Verification Criteria"
    例如 dev.backend → "### dev backend Verification Criteria"
    返回该标题到下一同级标题之间的全部行（含表头行）。

    Returns:
        提取的文本块，未找到时返回空字符串。
    """
    design_path = os.path.join(change_root, "design.md")
    if not os.path.isfile(design_path):
        return ""

    stage = PipelineState.extract_stage(track)
    bare = track.rsplit(".", 1)[-1] if "." in track else track
    target = f"### {stage} {bare} Verification Criteria"

    with open(design_path, encoding="utf-8") as f:
        lines = f.readlines()

    in_section = False
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("### "):
            if in_section:
                break
            if stripped == target:
                in_section = True
                continue
        if in_section:
            result.append(line)

    return "".join(result).rstrip()


def _read_scenario_yaml(change_root: str, filename: str = "scenario.yaml") -> str:
    """v3.6: 读取 .pg/changes/<change>/<filename> 全文。

    默认为 scenario.yaml（兼容旧 change），v3.6 起为 scenario-<track>.yaml。
    scenario.yaml 是 pg-propose 阶段生成，scenario-execute agent 的唯一输入。
    文件不存在时返回空字符串，scenario-execute agent 必须自己处理缺失场景。
    """
    if not change_root:
        return ""
    scenario_path = os.path.join(change_root, filename)
    if not os.path.isfile(scenario_path):
        return ""
    try:
        with open(scenario_path, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def build_ctx(
    state: PipelineState,
    track: str,
    phase: str,
    cycle: int = 1,
    change_root: str = "",
    project_root: str = "",
) -> dict[str, Any]:
    """构建 dispatch 上下文 dict。

    从 PipelineState 的 TrackState 中提取 sub-agent 所需的配置字段。
    如果 TrackState 来自旧快照（富化字段为空），则惰性从 project.yaml
    和 tasks.md 现场解析——确保即使绕过 _first_next() 也能拿到正确内容。

    Args:
        project_root: 项目根目录（用于读取 project.yaml），
                      优先于模块级缓存。
    """
    t = state.tracks.get(track, TrackState.create(track))
    ph = t.phases.get(phase, PhaseState())

    # 确保 project root 已缓存，供后续 build_rules 解析（#258）
    # 以及惰性富化环节共用。移出 needs_lazy 块以避免 post-bootstrap
    # 快照场景下 build_ctx 跳过惰性分支时 config cache 为空。
    if project_root:
        _set_project_root(project_root)

    # === 惰性富化：TrackState 来自旧快照时现场解析 ===
    needs_lazy = not (t.module_roots or t.module_details or t.test_commands)
    if needs_lazy:
        pc = _load_project_config_cached()
        module_names = list(t.modules)
        stage_name = track.rsplit(".", 1)[0] if "." in track else "dev"
        env_name = state.stage_env_map.get(stage_name, "dev-local")

        if pc:
            lazy_module_roots = resolve_module_roots(pc, module_names) or "[]"
            lazy_module_details = resolve_module_details(pc, module_names) or ""
            lazy_test_commands = resolve_test_commands(pc, module_names) or ""
            lazy_env_instances = resolve_env_instances(pc, env_name) or ""
            lazy_hooks_yaml = resolve_hooks(pc, env_name) or ""
            lazy_env_name = env_name
        else:
            lazy_module_roots = "[]"
            lazy_module_details = ""
            lazy_test_commands = ""
            lazy_env_instances = ""
            lazy_hooks_yaml = ""
            lazy_env_name = "dev-local"
    else:
        lazy_module_roots = t.module_roots or "[]"
        lazy_module_details = t.module_details or ""
        lazy_test_commands = t.test_commands or ""
        lazy_env_instances = t.env_instances_yaml or ""
        lazy_hooks_yaml = t.hooks_yaml or ""
        lazy_env_name = t.env_name or "dev-local"

    # === 惰性 tasks：tasks.md 内容为空时现场读取 ===
    tasks_preformatted = t.tasks_by_phase.get(phase, "")
    if not tasks_preformatted and change_root:
        tasks_preformatted = extract_section_content(change_root, track, phase)

    # tasks_validation: 来自 design.md 的 Verification Criteria 章节
    # 对所有 phase 注入，给 sub-agent 明确的验收标准（V-* 验证表）
    tasks_validation = ""
    if change_root:
        tasks_validation = extract_design_verification_criteria(change_root, track)

    # commands_normalized — simple track 的命令列表
    commands = t.commands if hasattr(t, 'commands') else ()
    if not commands and change_root:
        # 惰性从 execution-manifest.yaml 加载（兼容旧快照）
        import yaml as _yaml
        m_path = os.path.join(change_root, "execution-manifest.yaml")
        if os.path.isfile(m_path):
            try:
                with open(m_path, encoding="utf-8") as mf:
                    mdata = _yaml.safe_load(mf) or {}
                for stage in mdata.get("stages", []):
                    for trk in stage.get("tracks", []):
                        if isinstance(trk, dict) and trk.get("id") == t.bare:
                            cmds = trk.get("commands", [])
                            if cmds:
                                commands = tuple(cmds)
                            break
            except Exception:
                pass
    if commands:
        cmds_lines = []
        for i, cmd in enumerate(commands, 1):
            cmds_lines.append(f"  {i}. cmd: \"{cmd}\"")
            cmds_lines.append(f"     timeout_seconds: {t.timeout_seconds}")
        commands_normalized = "\n".join(cmds_lines)
    else:
        commands_normalized = ""

    # === gate_report_path — fix-gate 用，从 track 的 gate phase 报告路径读取 ===
    _gate_report_path = ""
    if phase in ("fix-gate",) and track in state.tracks:
        _gp = state.tracks[track].phases.get("gate")
        if _gp and _gp.report_path:
            _gate_report_path = _gp.report_path

    # === build_rules prompt injection — 从 project.yaml 读取 build_rules，
    #     按 target_agent（"pg-build/{phase}"）匹配，返回 (prepend, append) 文本。
    #     renderer 会在模板渲染完成后把 prepend/append 拼接到最终 prompt 前后。
    #     当 project_config 为空或无匹配规则时返回 ("", "")，不会影响渲染。
    target_agent = f"pg-build/{phase}"
    _build_rules_prepend, _build_rules_append = resolve_build_rules(
        _load_project_config_cached() or {}, target_agent,
    )

    # v3.6: per-track scenario-<track>.yaml (兼容旧 scenario.yaml)
    _scenario_filename = f"scenario-{track}.yaml"
    if not os.path.isfile(os.path.join(change_root, _scenario_filename)):
        _scenario_filename = "scenario.yaml"

    ctx: dict[str, Any] = {
        "_change": state.change,
        "id": track,
        "bare": t.bare,
        "label": t.label or track,
        "modules": list(t.modules),
        "module_roots": lazy_module_roots,
        "module_details": lazy_module_details,
        "max_fix_retries": t.max_fix_retries,
        # stage
        "stage_name": track.rsplit(".", 1)[0] if "." in track else "dev",
        "gate": "all_pass",
        "env_required": True,
        "env_name": lazy_env_name,
        "prepare_status": t.prepare_status or "ok",
        "prepare_log_path": t.prepare_log_path or "",
        "test_commands": lazy_test_commands,
        "env_instances": lazy_env_instances,
        "env_instances_block": _format_yaml_block("stage.environment.instances", lazy_env_instances),
        "hooks_block": _format_yaml_block("stage.environment.hooks", lazy_hooks_yaml),
        "hooks_yaml": lazy_hooks_yaml,
        # phase
        "phase": phase,
        "phase_allowed_statuses": PHASE_ALLOWED_STATUSES.get(phase, "completed | failed"),
        "cycle": cycle,
        "attempt": ph.attempt or 1,
        # verify / gate — report_filename 由 build_action 动态分配 seq 后覆盖
        "report_filename": f"{track}-{phase}-report.md",
        "report_seq": 1,
        # fix
        "fix_cycle": cycle,
        "verify_report_path": "",
        "fix_report_filename": f"{track}-{phase}-fix-{cycle}.md",
        # v3.6: per-track scenario-<track>.yaml (兼容旧 scenario.yaml)
        "scenario_yaml_path": os.path.join(change_root, _scenario_filename),
        "scenario_yaml_content": _read_scenario_yaml(change_root, _scenario_filename),
        # fix-gate
        "gate_report_path": _gate_report_path,
        "gate_cycles": cycle,
        "cycles_remaining": max(0, t.max_gate_fix_retries - cycle + 1),
        "max_gate_fix_retries": t.max_gate_fix_retries,
        # simple
        "track_timeout": t.timeout_seconds,
        "commands_normalized": commands_normalized,
        # final-gate
        "proposal_path": "",
        "tasks_path": "",
        "design_doc_paths": "",
        "report_paths": "",
        # tasks
        "tasks_preformatted": tasks_preformatted,
        "tasks_validation": tasks_validation,
        # build_rules prompt injection — 由 renderer 在 prompt 拼接时使用
        "build_rules_prepend": _build_rules_prepend,
        "build_rules_append": _build_rules_append,
        # === P0-A (v2.7)：父报告路径 + ROLLBACK 上下文注入 ===
        # 新增字段：code_view_report_path, failed_at, source, escalation_reason,
        # failed_v_tasks_inline。旧字段 verify_report_path 也回填。
        "code_view_report_path": "",
        "failed_at": "",
        "source": "",
        "escalation_reason": "",
        "failed_v_tasks_inline": "",
    }

    # === P0-A (v2.7)：从 current_sub_pipeline 注入父报告路径 + ROLLBACK 上下文 ===
    # 修复 5 个原本被硬编码空串的占位符：
    #   {verify_report_path} / {code_view_report_path} — fix/fix-review dispatch
    #   {failed_at} / {reason} / {source} — ROLLBACK CONTEXT 块
    # 任一字段为空时，模板侧会显示「（未提供）」，便于审计。
    if state.current_sub_pipeline is not None:
        sp = state.current_sub_pipeline
        # 父报告路径（绝对路径）：dispatch fix 时是 verify.md，fix-review 时是 review.md
        ctx["verify_report_path"] = sp.parent_report_path or ""
        # gate fix 时同样需要父 gate report（已知 _gate_report_path 已从 state 取）
        if phase == "fix-gate" and not ctx["gate_report_path"]:
            ctx["gate_report_path"] = sp.parent_report_path or ""
        # code view（review 子 pipeline 的父报告）= sp.parent_report_path
        if sp.kind == "review-cycle":
            ctx["code_view_report_path"] = sp.parent_report_path or ""

        # failed_v_tasks_inline: 拼成 "V-backend-1, V-backend-3" 形式给任务清单用
        if sp.failed_v_tasks:
            ctx["failed_v_tasks_inline"] = ", ".join(sp.failed_v_tasks)

        # rollback_context 字段
        ctx["failed_at"] = sp.created_at or ""
        ctx["source"] = sp.parent_phase or ""
        ctx["escalation_reason"] = sp.escalation_reason or ""
        # rollback.yaml 模板使用 {reason} 占位符（而非 {escalation_reason}），
        # 提供顶层别名以兼容旧模板。
        ctx["reason"] = sp.escalation_reason or f"{sp.kind} cycle {sp.cycle}"

        # 保留旧的 rollback_context dict 形式以避免破坏既有模板渲染
        ctx["rollback_context"] = {
            "failed_at": ctx["failed_at"],
            "reason": ctx["reason"],
            "source": ctx["source"],
        }

    # === v3.x: 加载 code review 规则文档（修复 dispatcher 死代码）===
    # 之前 load_markdown_rule 已实现但 dispatch.py 从未调用，导致 review agent
    # 看不到 .pg/code-review/<profile>/*.md 的具体 FAIL 判定。本段对每个
    # enabled check 调一次 load_markdown_rule，把 markdown 内容塞进 ctx。
    if phase == "review" and project_root:
        try:
            from pipeline.profile_loader import (
                resolve_profile_for_track,
                load_markdown_rule,
            )
            effective_profile = resolve_profile_for_track(
                project_root=project_root,
                track_code_review_profiles=t.code_review_profiles,
                track_code_review_profile=t.code_review_profile,
                track_code_review_languages=t.code_review_languages,
            )
            rule_docs: dict[str, str] = {}
            for check_name, _check_cfg in effective_profile.checks:
                content = load_markdown_rule(
                    project_root, effective_profile.name, check_name
                )
                if content:
                    rule_docs[check_name] = content
            ctx["code_review_rule_docs"] = rule_docs
            ctx["code_review_p0_checks"] = list(effective_profile.p0_check_names())
            ctx["code_review_rule_docs_yaml"] = _format_rule_docs_block(rule_docs)
        except Exception as exc:  # noqa: BLE001 — 容错：不阻断 dispatch
            ctx["code_review_rule_docs"] = {}
            ctx["code_review_p0_checks"] = []
            ctx["code_review_rule_docs_yaml"] = (
                f"⚠️ 加载规则文档失败: {type(exc).__name__}: {exc}"
            )

    return ctx


def _format_rule_docs_block(rule_docs: dict[str, str]) -> str:
    """把 rule_docs 字典格式化为 markdown 块，便于 review agent prompt 渲染。"""
    if not rule_docs:
        return "（未提供规则文档，请按通用 prompt 执行 review）"
    lines: list[str] = []
    for name in sorted(rule_docs.keys()):
        lines.append(f"#### {name}\n")
        lines.append(rule_docs[name].strip())
        lines.append("")  # 空行分隔
    return "\n".join(lines)


def build_action(
    state: PipelineState,
    action: PipelineAction,
    change_root: str,
) -> dict[str, Any]:
    """把 PipelineAction 转为标准 action JSON，并写 dispatch_file。"""
    track = action.track
    phase = action.phase
    cycle = action.cycle
    agent = PHASE_AGENTS.get(phase, action.agent)

    # 计算 project root（用于惰性富化时的 project.yaml 读取）
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(change_root)))

    # 构建上下文
    ctx = build_ctx(state, track, phase, cycle, change_root=change_root, project_root=project_root)

    # 分配全局 seq
    dispatch_seq = _allocate_seq(change_root)
    report_seq = dispatch_seq
    ds = _format_seq(dispatch_seq)
    rs = _format_seq(report_seq)
    ctx["dispatch_seq"] = ds
    ctx["report_seq"] = rs
    ctx["report_filename"] = f"{rs}-{track}-{phase}.md"
    ctx["fix_report_filename"] = f"{rs}-{track}-fix-{cycle}.md"  # v2.2: 去掉冗余 phase 字段

    # v2.4: 派生 result JSON 路径（与 dispatch_file 同前缀，扩展名 .result.json）
    # dispatch_file 命名规则: <seq>-<track>-<phase>-dispatch[-cycle].md
    # result 命名规则:     <seq>-<track>-<phase>-result[-cycle].json
    result_prefix = f"{ds}-{track}-{phase}-result"
    if cycle > 1:
        result_prefix += f"-{cycle}"
    expected_result_path = os.path.join(change_root, "2-build", f"{result_prefix}.json")
    ctx["result_json_path"] = expected_result_path  # 注入 prompt，供 sub-agent 使用

    # 写 dispatch_file
    filepath = render_dispatch_file(
        change_root=change_root,
        track=track,
        phase=phase,
        ctx=ctx,
        cycle=cycle,
        dispatch_seq=ds,
    )

    result: dict[str, Any] = {
        "action": "dispatch",
        "item": track,
        "sub": phase,
        "agent": agent,
        "cycle": cycle,
        "dispatch_seq": ds,
        "report_seq": rs,
        "dispatch_file": filepath,
        "expected_result_path": expected_result_path,  # v2.4 新增
    }
    return result


def build_final_gate_action(
    state: PipelineState,
    change_root: str,
) -> dict[str, Any]:
    """构建 final-gate dispatch action。"""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(change_root)))
    ctx = build_ctx(state, "final-gate", "gate", change_root=change_root, project_root=project_root)

    # 分配全局 seq
    dispatch_seq = _allocate_seq(change_root)
    report_seq = dispatch_seq
    ds = _format_seq(dispatch_seq)
    rs = _format_seq(report_seq)
    ctx["dispatch_seq"] = ds
    ctx["report_seq"] = rs
    ctx["report_filename"] = f"{rs}-final-gate-gate-report.md"

    # 补充 final-gate 专有字段
    ctx["proposal_path"] = os.path.join(change_root, "proposal.md")
    ctx["tasks_path"] = os.path.join(change_root, "tasks.md")
    ctx["design_doc_paths"] = os.path.join(change_root, "design.md")

    # 扫描 2-build/ 下所有 gate assessment 报告
    build_dir = os.path.join(change_root, "2-build")
    report_paths: list[str] = []
    if os.path.isdir(build_dir):
        for fname in sorted(os.listdir(build_dir)):
            if fname.endswith("-gate-assessment.md") or fname.endswith("-gate-report.md"):
                report_paths.append(os.path.join(build_dir, fname))
    ctx["report_paths"] = "\n".join(report_paths)

    # v2.4: 派生 final-gate result JSON 路径
    expected_result_path = os.path.join(
        change_root, "2-build", f"{ds}-final-gate-gate-result.json"
    )
    ctx["result_json_path"] = expected_result_path

    filepath = render_dispatch_file(
        change_root=change_root,
        track="final-gate",
        phase="gate",
        ctx=ctx,
        dispatch_seq=ds,
    )

    return {
        "action": "dispatch_final_gate",
        "item": "final-gate",
        "agent": FINAL_GATE_AGENT,
        "dispatch_seq": ds,
        "report_seq": rs,
        "dispatch_file": filepath,
        "expected_result_path": expected_result_path,  # v2.4 新增
    }