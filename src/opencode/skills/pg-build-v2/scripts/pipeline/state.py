"""PipelineState: pg-build-v2 内存中的 pipeline 状态。

设计原则：
- frozen dataclass: 不可变。状态变更通过 reducer 生成新对象。
- dict 兼容: 可与旧 .pipeline-state.json 字段名一一对应。
- snapshot 持久化: 通过 to_dict/from_dict 序列化到 disk。
- SubPipeline 从 sub_pipeline.py 导入, 不在本模块定义。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SUB_PHASES: tuple[str, ...] = ("test", "dev", "verify", "gate")
SUB_PHASES_WITH_FIX: tuple[str, ...] = ("test", "dev", "verify", "gate")
FIX_SUB = "fix"
FIX_GATE_SUB = "fix-gate"
SIMPLE_SUB = "simple"


@dataclass(frozen=True)
class PhaseState:
    """单个 (track, phase) 的状态。"""

    status: str = "pending"  # pending | running | completed | failed | pass | skipped
    attempt: int = 0
    started_at: str | None = None
    completed_at: str | None = None
    agent: str | None = None
    report_path: str | None = None
    summary: str = ""
    tasks_marked: tuple[int, ...] = ()
    cycles: tuple[dict[str, Any], ...] = ()
    fix_cycles: tuple[dict[str, Any], ...] = ()
    gate_cycles: tuple[dict[str, Any], ...] = ()
    fix_gates: tuple[dict[str, Any], ...] = ()
    current_cycle: int = 1

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status": self.status,
            "attempt": self.attempt,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "agent": self.agent,
            "report_path": self.report_path,
            "summary": self.summary,
            "tasks_marked": list(self.tasks_marked),
            "current_cycle": self.current_cycle,
        }
        if self.cycles:
            out["cycles"] = list(self.cycles)
        if self.fix_cycles:
            out["fix_cycles"] = list(self.fix_cycles)
        if self.gate_cycles:
            out["gate_cycles"] = list(self.gate_cycles)
        if self.fix_gates:
            out["fix_gates"] = list(self.fix_gates)
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PhaseState":
        return cls(
            status=d.get("status", "pending"),
            attempt=d.get("attempt", 0),
            started_at=d.get("started_at"),
            completed_at=d.get("completed_at"),
            agent=d.get("agent"),
            report_path=d.get("report_path"),
            summary=d.get("summary", ""),
            tasks_marked=tuple(d.get("tasks_marked", ())),
            cycles=tuple(d.get("cycles", [])),
            fix_cycles=tuple(d.get("fix_cycles", [])),
            gate_cycles=tuple(d.get("gate_cycles", [])),
            fix_gates=tuple(d.get("fix_gates", [])),
            current_cycle=d.get("current_cycle", 1),
        )

    def replace(self, **kwargs: Any) -> "PhaseState":
        """返回新 PhaseState（仅替换指定字段）。"""
        return PhaseState(
            status=kwargs.get("status", self.status),
            attempt=kwargs.get("attempt", self.attempt),
            started_at=kwargs.get("started_at", self.started_at),
            completed_at=kwargs.get("completed_at", self.completed_at),
            agent=kwargs.get("agent", self.agent),
            report_path=kwargs.get("report_path", self.report_path),
            summary=kwargs.get("summary", self.summary),
            tasks_marked=kwargs.get("tasks_marked", self.tasks_marked),
            cycles=kwargs.get("cycles", self.cycles),
            fix_cycles=kwargs.get("fix_cycles", self.fix_cycles),
            gate_cycles=kwargs.get("gate_cycles", self.gate_cycles),
            fix_gates=kwargs.get("fix_gates", self.fix_gates),
            current_cycle=kwargs.get("current_cycle", self.current_cycle),
        )



@dataclass(frozen=True)
class TrackState:
    """单个 track 的状态。"""

    track_id: str
    bare: str
    label: str = ""
    status: str = "pending"  # pending | running | completed | failed | skipped
    started_at: str | None = None
    completed_at: str | None = None
    modules: tuple[str, ...] = ()
    max_fail_retries: int = 3
    max_fix_retries: int = 5
    max_gate_fix_retries: int = 2
    phases: dict[str, PhaseState] = field(default_factory=dict)
    sub_pipelines: tuple[Any, ...] = ()  # SubPipeline
    accepted_gaps: tuple[dict[str, Any], ...] = ()

    @classmethod
    def create(cls, track_id: str, **kwargs) -> "TrackState":
        """工厂方法：bare 自动从 track_id 派生。"""
        bare = kwargs.pop("bare", None) or track_id.rsplit(".", 1)[-1]
        return cls(track_id=track_id, bare=bare, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "bare": self.bare,
            "label": self.label,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "modules": list(self.modules),
            "max_fail_retries": self.max_fail_retries,
            "max_fix_retries": self.max_fix_retries,
            "max_gate_fix_retries": self.max_gate_fix_retries,
            "phases": {k: v.to_dict() for k, v in self.phases.items()},
            "sub_pipelines": [sp.to_dict() for sp in self.sub_pipelines],
            "accepted_gaps": list(self.accepted_gaps),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TrackState":
        from pipeline.sub_pipeline import SubPipeline

        phases = {k: PhaseState.from_dict(v) for k, v in d.get("phases", {}).items()}
        sub_pipelines = tuple(
            SubPipeline.from_dict(sp) for sp in d.get("sub_pipelines", [])
        )
        return cls(
            track_id=d["track_id"],
            bare=d.get("bare", d["track_id"]),
            label=d.get("label", ""),
            status=d.get("status", "pending"),
            started_at=d.get("started_at"),
            completed_at=d.get("completed_at"),
            modules=tuple(d.get("modules", ())),
            max_fail_retries=d.get("max_fail_retries", 3),
            max_fix_retries=d.get("max_fix_retries", 5),
            max_gate_fix_retries=d.get("max_gate_fix_retries", 2),
            phases=phases,
            sub_pipelines=sub_pipelines,
            accepted_gaps=tuple(d.get("accepted_gaps", ())),
        )

    def replace(self, **kwargs: Any) -> "TrackState":
        return TrackState(
            track_id=kwargs.get("track_id", self.track_id),
            bare=kwargs.get("bare", self.bare),
            label=kwargs.get("label", self.label),
            status=kwargs.get("status", self.status),
            started_at=kwargs.get("started_at", self.started_at),
            completed_at=kwargs.get("completed_at", self.completed_at),
            modules=kwargs.get("modules", self.modules),
            max_fail_retries=kwargs.get("max_fail_retries", self.max_fail_retries),
            max_fix_retries=kwargs.get("max_fix_retries", self.max_fix_retries),
            max_gate_fix_retries=kwargs.get("max_gate_fix_retries", self.max_gate_fix_retries),
            phases=kwargs.get("phases", self.phases),
            sub_pipelines=kwargs.get("sub_pipelines", self.sub_pipelines),
            accepted_gaps=kwargs.get("accepted_gaps", self.accepted_gaps),
        )

    def get_phase(self, phase: str) -> PhaseState:
        return self.phases.get(phase, PhaseState())


@dataclass(frozen=True)
class PipelineState:
    """整个 pipeline 的状态。"""

    schema_version: str = "2026-06-30"
    change: str = ""
    pipeline_order: tuple[str, ...] = ()
    track_types: dict[str, str] = field(default_factory=dict)
    tracks: dict[str, TrackState] = field(default_factory=dict)
    current_sub_pipeline: Any | None = None  # SubPipeline
    init_committed: bool = False
    init_commit_sha: str | None = None
    feature_branch: str | None = None
    status: str = "pending"  # pending | running | completed | failed
    failed_reason: str | None = None
    current_track: str = ""
    current_phase: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schema_version": self.schema_version,
            "change": self.change,
            "pipeline_order": list(self.pipeline_order),
            "track_types": dict(self.track_types),
            "tracks": {k: v.to_dict() for k, v in self.tracks.items()},
            "init_committed": self.init_committed,
            "init_commit_sha": self.init_commit_sha,
            "feature_branch": self.feature_branch,
            "status": self.status,
            "failed_reason": self.failed_reason,
            "current_track": self.current_track,
            "current_phase": self.current_phase,
        }
        if self.current_sub_pipeline is not None:
            if hasattr(self.current_sub_pipeline, "to_dict"):
                out["current_sub_pipeline"] = self.current_sub_pipeline.to_dict()
            else:
                out["current_sub_pipeline"] = {
                    "id": self.current_sub_pipeline.get("id", ""),
                    "parent_track": self.current_sub_pipeline.get("parent_track", ""),
                }
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PipelineState":
        from pipeline.sub_pipeline import SubPipeline

        tracks = {k: TrackState.from_dict(v) for k, v in d.get("tracks", {}).items()}
        current_sp = None
        if d.get("current_sub_pipeline"):
            sp_dict = d["current_sub_pipeline"]
            try:
                current_sp = SubPipeline.from_dict(sp_dict)
            except Exception:
                pass
        return cls(
            schema_version=d.get("schema_version", "2026-06-30"),
            change=d.get("change", ""),
            pipeline_order=tuple(d.get("pipeline_order", ())),
            track_types=d.get("track_types", {}),
            tracks=tracks,
            current_sub_pipeline=current_sp,
            init_committed=d.get("init_committed", False),
            init_commit_sha=d.get("init_commit_sha"),
            feature_branch=d.get("feature_branch"),
            status=d.get("status", "pending"),
            failed_reason=d.get("failed_reason"),
            current_track=d.get("current_track", ""),
            current_phase=d.get("current_phase", ""),
        )

    def replace(self, **kwargs: Any) -> "PipelineState":
        return PipelineState(
            schema_version=kwargs.get("schema_version", self.schema_version),
            change=kwargs.get("change", self.change),
            pipeline_order=kwargs.get("pipeline_order", self.pipeline_order),
            track_types=kwargs.get("track_types", self.track_types),
            tracks=kwargs.get("tracks", self.tracks),
            current_sub_pipeline=kwargs.get("current_sub_pipeline", self.current_sub_pipeline),
            init_committed=kwargs.get("init_committed", self.init_committed),
            init_commit_sha=kwargs.get("init_commit_sha", self.init_commit_sha),
            feature_branch=kwargs.get("feature_branch", self.feature_branch),
            status=kwargs.get("status", self.status),
            failed_reason=kwargs.get("failed_reason", self.failed_reason),
            current_track=kwargs.get("current_track", self.current_track),
            current_phase=kwargs.get("current_phase", self.current_phase),
        )

    def is_track_completed(self, track_id: str) -> bool:
        t = self.tracks.get(track_id)
        return t is not None and t.status == "completed"

    def all_tracks_completed(self) -> bool:
        if not self.pipeline_order:
            return False
        return all(self.is_track_completed(t) for t in self.pipeline_order)