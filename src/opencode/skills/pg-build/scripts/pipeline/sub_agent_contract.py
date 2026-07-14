"""Sub-agent 返回契约校验。

v2.1 引入：所有 sub-agent 必须按 Sub-agent 返回契约返回 JSON。
orchestrator.record() 调用本模块校验 CLI 参数（从 sub-agent 返回中提取），
缺失/类型错误触发 hard fail，返回 error action 给编排器。

校验失败时不写 event log、不推进 state——直接返回 error action。
"""

from __future__ import annotations

import os
from typing import Any

from pipeline.events import (
    STATUSES_ALL,
    STATUS_ESCALATE,  # v2.2: escalate 校验
    PHASE_STATUS_ALLOWED,
    SUB_GATE,
    SUB_REVIEW,
    FINAL_GATE_TRACK,
)


# 各 phase 的额外要求
# tasks_updated_required:
#   True         -> 必填
#   False        -> 选填
#   "escalate_only" -> 仅当 status=escalate 时必填
PHASE_RULES: dict[str, dict[str, Any]] = {
    "verify": {
        "evidence_required": True,
        "report_required": True,
        "tasks_updated_required": "escalate_only",
    },
    "gate": {
        "evidence_required": True,
        "report_required": True,
        "tasks_updated_required": False,
    },
    "fix-gate": {
        "evidence_required": True,
        "report_required": True,
        "tasks_updated_required": True,
    },
    # final-gate 的 phase 也是 "gate"，但 track 是 "final-gate"
    # track 名校验由 caller 决定
    "fix": {
        "evidence_required": False,
        "report_required": True,  # v2.2: fix 阶段强制要求 report（追溯修复证据）
        "outputs_required": True,  # v2.2: fix 阶段 outputs 必填
        "tasks_updated_required": True,
    },
    "test": {
        "evidence_required": False,
        "report_required": False,
        "outputs_required": True,  # v2.2: test 阶段 outputs 必填（产物列表）
        "tasks_updated_required": True,
    },
    "dev": {
        "evidence_required": False,
        "report_required": False,
        "outputs_required": True,  # v2.2: dev 阶段 outputs 必填（产物列表）
        "tasks_updated_required": True,
    },
    # v2.6: review phase（dev 后 verify 前的静态代码审查）
    "review": {
        "evidence_required": True,
        "report_required": True,
        "tasks_updated_required": "escalate_only",
        "review_score_required": True,  # summary 必须含 review_score
    },
    "fix-review": {
        "evidence_required": False,
        "report_required": True,  # 与 fix 一致：修复记录必填
        "outputs_required": True,
        "tasks_updated_required": True,
    },
    "simple": {
        "evidence_required": False,
        "report_required": False,
        "outputs_required": False,
        "tasks_updated_required": False,
    },
}


def _is_empty_tasks(tasks_updated) -> bool:
    """严格空检测：空数组 / 单空字符串 / 全空白字符串都视为未填。"""
    if not tasks_updated:
        return True
    return all(not (t and t.strip()) for t in tasks_updated)


def validate_record_args(
    phase: str,
    track: str,
    status: str,
    summary: str,
    report_path: str,
    outputs: str,
    issues: str = "",
    evidence_paths: tuple[str, ...] | list[str] = (),
    tasks_updated: tuple[str, ...] | list[str] = (),
) -> tuple[bool, str]:
    """校验 record CLI 参数是否满足 sub-agent 返回契约。

    Args:
        phase: 当前 phase（test / dev / verify / gate / fix / fix-gate / simple）
        track: 当前 track id（含 stage 前缀，如 dev.backend）
        status: record status
        summary: 一句话摘要（1-200 字）
        report_path: 验证/审查报告路径
        outputs: 产物文件列表（逗号分隔）
        issues: 问题列表（逗号分隔）
        evidence_paths: 证据文件路径列表
        tasks_updated: v2.2 — 已更新的 task_id 列表（escalate 时必填）

    Returns:
        (ok, reason):
            ok=True → 通过
            ok=False → reason 是失败原因（含字段名 + 修复建议）
    """
    # ── summary 必填且长度合法 ──
    if not summary or not summary.strip():
        return False, (
            "schema_violation: 缺少 summary（sub-agent 必须返回一句话摘要）"
        )
    if len(summary) > 200:
        return False, (
            f"schema_violation: summary 长度 {len(summary)} 超过 200 字上限"
        )

    # ── status 必须合法（v2.1: 从 pipeline.events.STATUSES_ALL 单一来源）──
    if status not in STATUSES_ALL:
        return False, (
            f"schema_violation: status={status!r} 不在 {sorted(STATUSES_ALL)}"
        )

    # ── phase-specific 规则 ──
    # final-gate 的 phase 是 "gate" 但 track 是 "final-gate"，
    # 二者规则一致：都要 evidence + report_path
    if track == FINAL_GATE_TRACK:
        rule_track = SUB_GATE  # final-gate 复用 gate 的规则
    else:
        rule_track = phase
    rule = PHASE_RULES.get(rule_track, {})

    # ── v2.1: status 必须与 phase 兼容 ──
    # final-gate 用 phase="gate" 但 track="final-gate"，
    # 二者都用同一 PHASE_STATUS_ALLOWED["gate"] 规则集（pass/fail）
    allowed_for_phase = PHASE_STATUS_ALLOWED.get(rule_track, frozenset())
    if allowed_for_phase and status not in allowed_for_phase:
        return False, (
            f"schema_violation: phase={phase} (track={track}) 不允许 status={status!r},"
            f" 允许 {sorted(allowed_for_phase)}"
        )

    # evidence 检查：verify / gate / fix-gate / final-gate 要求 evidence 非空
    # CLI 协议下 evidence = outputs + report_path
    if rule.get("evidence_required"):
        effective_evidence = list(evidence_paths) if evidence_paths else []
        # 没有显式 evidence 时，fallback 用 outputs（视为 evidence）
        if not effective_evidence and outputs:
            effective_evidence = [s.strip() for s in outputs.split(",") if s.strip()]
        # report_path 也算 evidence
        if report_path and report_path not in effective_evidence:
            effective_evidence.append(report_path)

        if not effective_evidence:
            return False, (
                f"evidence_missing: phase={phase} (track={track}) 要求 evidence 非空，"
                f"请让 sub-agent 产出可追溯证据文件（测试日志 / 命令输出 / 日志片段）"
            )

    # report_path 检查
    if rule.get("report_required"):
        if not report_path or not report_path.strip():
            return False, (
                f"schema_violation: phase={phase} (track={track}) 要求 report_path，"
                f"请让 sub-agent 将报告写入 2-build/<seq>-<track>-<phase>.md"
            )
        if not os.path.isfile(report_path):
            return False, (
                f"report_missing: report_path 指向的文件不存在: {report_path}"
            )

    # ── v2.2: outputs 必填检查（test/dev/fix 阶段） ──
    if rule.get("outputs_required"):
        if not outputs or not outputs.strip():
            return False, (
                f"schema_violation: phase={phase} (track={track}) 要求 --outputs 非空，"
                f"请让 sub-agent 返回产物文件列表（逗号分隔的绝对路径）"
            )

    # ── v2.3: tasks_updated 必填检查（按 phase 区分）──
    tasks_required = rule.get("tasks_updated_required")
    if tasks_required == "escalate_only":
        if status == STATUS_ESCALATE and _is_empty_tasks(tasks_updated):
            return False, (
                "schema_violation: verify escalate 要求 --tasks-updated 非空，"
                "请填写失败的 V-* ID 列表。Example: --tasks-updated 'V-backend-1,V-backend-3'"
            )
    elif tasks_required is True:
        if _is_empty_tasks(tasks_updated):
            guidance = {
                "test": "test 红 phase 必填 --tasks-updated 列出覆盖的 task_id。Example: --tasks-updated '10.1,10.2'",
                "dev":  "dev phase 必填 --tasks-updated 列出实现的 task_id。Example: --tasks-updated '2.1,2.3'",
                "fix":  "fix phase 必填 --tasks-updated 列出修复的 V-* 或 task_id。Example: --tasks-updated 'V-backend-6,3.9'",
                "fix-gate": "fix-gate phase 必填 --tasks-updated 列出修复的 task_id。",
            }
            return False, (
                f"schema_violation: phase={phase} (track={track}) 必填 --tasks-updated 非空。"
                f"{guidance.get(phase, '')} "
                f"原因：空值会触发 orchestrator 全段标记 fallback，污染 tasks.md。"
            )

    # ── v2.2: escalate 时强制 evidence（允许 outputs/report_path fallback） ──
    if status == STATUS_ESCALATE and phase in ("verify", "review"):
        effective_evidence = list(evidence_paths) if evidence_paths else []
        if not effective_evidence and outputs:
            effective_evidence = [s.strip() for s in outputs.split(",") if s.strip()]
        if report_path and report_path not in effective_evidence:
            effective_evidence.append(report_path)
        if not effective_evidence:
            phase_label = "review" if phase == "review" else "verify"
            return False, (
                f"schema_violation: escalate 要求 --evidence 非空（需包含 {phase_label} 报告路径），"
                f"请让 sub-agent 产出可追溯证据文件"
            )

    # ── v2.1: gate / final-gate 要求 summary 含 gate-score ──
    if rule_track == SUB_GATE:
        score = parse_gate_score(summary)
        if score is None:
            return False, (
                f"schema_violation: phase={phase} (track={track}) 要求 summary 中含 "
                f"'gate_score: <0-100>', 例如 'gate_score: 85, p0_failures: []'"
            )
        if not (0 <= score <= 100):
            return False, (
                f"schema_violation: gate_score={score} 不在 [0, 100] 范围"
            )

    # ── v2.6: review 要求 summary 含 review_score ──
    if rule_track == SUB_REVIEW:
        score = parse_gate_score(summary)  # 复用同一解析器：review_score 或 final_score
        if score is None:
            return False, (
                f"schema_violation: phase={phase} (track={track}) 要求 summary 中含 "
                f"'review_score: <0-100>', 例如 'review_score: 85, p0_failures: [R-1]'"
            )
        if not (0 <= score <= 100):
            return False, (
                f"schema_violation: review_score={score} 不在 [0, 100] 范围"
            )

    # ── outputs 中的路径：警告但不阻断（产物可能尚未落盘） ──
    if outputs:
        for p in (s.strip() for s in outputs.split(",")):
            if not p:
                continue
            # 只检查 path 格式合法性，不强制 file_exists
            if not isinstance(p, str) or "\x00" in p:
                return False, f"schema_violation: outputs 含非法路径: {p!r}"

    return True, ""


def parse_gate_score(summary: str) -> int | None:
    """从 summary 解析 gate_score / final_score / review_score。

    支持格式：
      - 'gate_score: 85, p0_failures: []'
      - 'gate_score=85'
      - 'final_score: 92, min_track_score: 80, p0_failures: [G-1]'
      - 'review_score: 90, p0_failures: []'  # v2.6: review 用

    Returns:
        int: 0-100 之间的分数
        None: 未找到或解析失败
    """
    import re
    # 同时匹配 gate_score / final_score / review_score
    m = re.search(r'(?:gate_score|final_score|review_score)\s*[=:]\s*(\d+)', summary)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def parse_p0_failures(summary: str) -> tuple[str, ...]:
    """从 summary 解析 p0_failures 列表。

    支持格式：
      - 'p0_failures: []'  → 空 tuple
      - 'p0_failures: [R-1, R-3]'  → ('R-1', 'R-3')
      - 'p0_failures: [G-1]'  → ('G-1',)

    Returns:
        tuple[str, ...]: 失败项 ID 列表；未找到时返回 ()
    """
    import re
    m = re.search(r'p0_failures\s*[=:]\s*\[([^\]]*)\]', summary)
    if not m:
        return ()
    raw = m.group(1).strip()
    if not raw:
        return ()
    items = [s.strip().strip("'\"") for s in raw.split(",") if s.strip()]
    return tuple(items)