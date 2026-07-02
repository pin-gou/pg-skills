"""Event types for pg-build-v2 pipeline events.

所有 event 在写入 pipeline.events 文件时序列化为 JSON。
此处定义的 dataclass 是类型化引用，序列化逻辑在 event_log.py。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# 5 个 sub 类型（与 v1 ALLOWED_STATUS 兼容）
SUB_TEST = "test"
SUB_DEV = "dev"
SUB_VERIFY = "verify"
SUB_GATE = "gate"
SUB_FIX = "fix"
SUB_FIX_GATE = "fix-gate"
SUB_SIMPLE = "simple"

ALL_SUBS: tuple[str, ...] = (
    SUB_TEST, SUB_DEV, SUB_VERIFY, SUB_GATE,
    SUB_FIX, SUB_FIX_GATE, SUB_SIMPLE,
)


# 5 个 record status（与 v1 ALLOWED_STATUS 兼容）
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_ESCALATE = "escalate"
STATUS_PASS = "pass"
STATUS_FAIL = "fail"

ALL_STATUSES: tuple[str, ...] = (
    STATUS_COMPLETED, STATUS_FAILED, STATUS_ESCALATE, STATUS_PASS, STATUS_FAIL,
)


# Phase type of final-gate (特殊 item)
FINAL_GATE_TRACK = "final-gate"
FINAL_GATE_PHASE = "gate"


@dataclass(frozen=True)
class PipelineRecord:
    """sub-agent 完成结果，由 LLM 通过 `record` 命令注入。"""

    track: str
    phase: str
    status: str
    summary: str = ""
    report_path: str | None = None
    issues: str = ""
    attempt: int = 1
    cycle: int = 1


@dataclass(frozen=True)
class PipelineAction:
    """reducer 输出：状态变更后的下一步动作。"""

    kind: str  # dispatch | dispatch_fix | advance | done | failed | error | workflow_failed | env_switch
    track: str = ""
    phase: str = ""
    cycle: int = 1
    attempt: int = 1
    agent: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "action": self.kind,
        }
        if self.track:
            out["item"] = self.track
        if self.phase:
            out["sub"] = self.phase
        if self.cycle > 1:
            out["cycle"] = self.cycle
        if self.agent:
            out["agent"] = self.agent
        if self.detail:
            out.update(self.detail)
        return out


# Event type 常量（写入 pipeline.events 时作为 "type" 字段值）
EVT_PIPELINE_STARTED = "pipeline_started"
EVT_BOOTSTRAP_STEP_COMPLETED = "bootstrap_step_completed"
EVT_PREPARE_ENV_STARTED = "prepare_env_started"
EVT_PREPARE_ENV_COMPLETED = "prepare_env_completed"
EVT_CLEAN_ENV_STARTED = "clean_env_started"
EVT_CLEAN_ENV_COMPLETED = "clean_env_completed"
EVT_DISPATCH_STARTED = "dispatch_started"
EVT_RECORD_RECEIVED = "record_received"
EVT_FIX_CYCLE_STARTED = "fix_cycle_started"
EVT_GATE_CYCLE_STARTED = "gate_cycle_started"
EVT_SUB_PIPELINE_COMPLETED = "sub_pipeline_completed"
EVT_TRACK_COMPLETED = "track_completed"
EVT_PIPELINE_COMPLETED = "pipeline_completed"
EVT_WORKFLOW_FAILED = "workflow_failed"
EVT_DISPATCH_ABANDONED = "dispatch_abandoned"
EVT_GIT_COMMIT = "git_commit"

ALL_EVENT_TYPES: tuple[str, ...] = (
    EVT_PIPELINE_STARTED, EVT_BOOTSTRAP_STEP_COMPLETED,
    EVT_PREPARE_ENV_STARTED, EVT_PREPARE_ENV_COMPLETED,
    EVT_CLEAN_ENV_STARTED, EVT_CLEAN_ENV_COMPLETED,
    EVT_DISPATCH_STARTED, EVT_RECORD_RECEIVED,
    EVT_FIX_CYCLE_STARTED, EVT_GATE_CYCLE_STARTED,
    EVT_SUB_PIPELINE_COMPLETED, EVT_TRACK_COMPLETED,
    EVT_PIPELINE_COMPLETED, EVT_WORKFLOW_FAILED, EVT_DISPATCH_ABANDONED, EVT_GIT_COMMIT,
)