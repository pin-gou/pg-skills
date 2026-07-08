"""PipelineState: pg-build 内存中的 pipeline 状态。

设计原则：
- frozen dataclass: 不可变。状态变更通过 reducer 生成新对象。
- dict 兼容: 可与旧 .pipeline-state.json 字段名一一对应。
- snapshot 持久化: 通过 to_dict/from_dict 序列化到 disk。
- SubPipeline 从 sub_pipeline.py 导入, 不在本模块定义。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any


SUB_PHASES: tuple[str, ...] = ("test", "dev", "review", "verify", "gate")
FIX_SUB = "fix"
FIX_GATE_SUB = "fix-gate"
SIMPLE_SUB = "simple"
REVIEW_SUB = "review"
FIX_REVIEW_SUB = "fix-review"


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
    review_fix_cycles: tuple[dict[str, Any], ...] = ()
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
        if self.review_fix_cycles:
            out["review_fix_cycles"] = list(self.review_fix_cycles)
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
            review_fix_cycles=tuple(d.get("review_fix_cycles", [])),
            gate_cycles=tuple(d.get("gate_cycles", [])),
            fix_gates=tuple(d.get("fix_gates", [])),
            current_cycle=d.get("current_cycle", 1),
        )

    def replace(self, **kwargs: Any) -> "PhaseState":
        return dataclasses.replace(self, **kwargs)



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
    max_review_fix_retries: int = 3
    phases: dict[str, PhaseState] = field(default_factory=dict)
    sub_pipelines: tuple[Any, ...] = ()  # SubPipeline
    accepted_gaps: tuple[dict[str, Any], ...] = ()
    # review 配置（v2.6: user-facing config fields）
    code_review_enabled: bool = True
    code_review_profiles: tuple[str, ...] = ()
    code_review_profile: str = ""
    code_review_languages: tuple[str, ...] = ()

    # 富化上下文（由 _first_next 从 project.yaml 预填充）
    module_roots: str = ""               # "[webvirt-backend, webvirt-agent-proto]"
    module_details: str = ""             # "- module: backend\n  - root: webvirt-backend\n..."
    test_commands: str = ""              # "cd webvirt-backend && mvn test"
    review_level: str = ""               # "security" | "standard" | "none"
    env_name: str = ""                   # "dev-local"
    env_instances_yaml: str = ""         # 环境实例的 YAML 文本
    hooks_yaml: str = ""                 # 环境 hooks 的 YAML 文本
    prepare_log_path: str = ""           # prepare_env 日志路径
    prepare_status: str = ""             # "ok" | "error" | "skipped"
    tasks_by_phase: dict[str, str] = field(default_factory=dict)
    commands: tuple[str, ...] = ()       # simple track 的命令列表
    timeout_seconds: int = 1800          # simple track 默认命令超时

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
            "max_review_fix_retries": self.max_review_fix_retries,
            "phases": {k: v.to_dict() for k, v in self.phases.items()},
            "sub_pipelines": [sp.to_dict() for sp in self.sub_pipelines],
            "accepted_gaps": list(self.accepted_gaps),
            "module_roots": self.module_roots,
            "module_details": self.module_details,
            "test_commands": self.test_commands,
            "review_level": self.review_level,
            "env_name": self.env_name,
            "env_instances_yaml": self.env_instances_yaml,
            "hooks_yaml": self.hooks_yaml,
            "prepare_log_path": self.prepare_log_path,
            "prepare_status": self.prepare_status,
            "tasks_by_phase": dict(self.tasks_by_phase),
            "commands": list(self.commands),
            "timeout_seconds": self.timeout_seconds,
            "code_review_enabled": self.code_review_enabled,
            "code_review_profiles": list(self.code_review_profiles),
            "code_review_profile": self.code_review_profile,
            "code_review_languages": list(self.code_review_languages),
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
            max_review_fix_retries=d.get("max_review_fix_retries", 3),
            phases=phases,
            sub_pipelines=sub_pipelines,
            accepted_gaps=tuple(d.get("accepted_gaps", ())),
            module_roots=d.get("module_roots", ""),
            module_details=d.get("module_details", ""),
            test_commands=d.get("test_commands", ""),
            review_level=d.get("review_level", ""),
            env_name=d.get("env_name", ""),
            env_instances_yaml=d.get("env_instances_yaml", ""),
            hooks_yaml=d.get("hooks_yaml", ""),
            prepare_log_path=d.get("prepare_log_path", ""),
            prepare_status=d.get("prepare_status", ""),
            tasks_by_phase=d.get("tasks_by_phase", {}),
            commands=tuple(d.get("commands", [])),
            timeout_seconds=d.get("timeout_seconds", 1800),
            code_review_enabled=d.get("code_review_enabled", True),
            code_review_profiles=tuple(d.get("code_review_profiles", ())),
            code_review_profile=d.get("code_review_profile", ""),
            code_review_languages=tuple(d.get("code_review_languages", ())),
        )

    def replace(self, **kwargs: Any) -> "TrackState":
        return dataclasses.replace(self, **kwargs)

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
    last_dispatch_file: str = ""       # P3: 上次 dispatch 的文件路径（重跑检测用）
    retry_count: int = 0               # P3: 当前 dispatch 的 retry 计数
    # stage 生命周期管理
    stage_order: tuple[str, ...] = ()               # ["dev", "integration"]
    stage_env_map: dict[str, str] = field(default_factory=dict)  # {"dev": "dev-local", "integration": "dev-3tier"}
    stage_env_timeout: dict[str, int] = field(default_factory=dict)  # {"dev-local": 600} hook timeout
    current_stage: str = ""
    stage_prepared: set[str] = field(default_factory=set)        # 已 prepare 的 stage 集合

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
            "last_dispatch_file": self.last_dispatch_file,
            "retry_count": self.retry_count,
            "stage_order": list(self.stage_order),
            "stage_env_map": dict(self.stage_env_map),
            "stage_env_timeout": dict(self.stage_env_timeout),
            "current_stage": self.current_stage,
            "stage_prepared": list(self.stage_prepared),
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
            last_dispatch_file=d.get("last_dispatch_file", ""),
            retry_count=d.get("retry_count", 0),
            stage_order=tuple(d.get("stage_order", ())),
            stage_env_map=d.get("stage_env_map", {}),
            stage_env_timeout=d.get("stage_env_timeout", {}),
            current_stage=d.get("current_stage", ""),
            stage_prepared=set(d.get("stage_prepared", [])),
        )

    def replace(self, **kwargs: Any) -> "PipelineState":
        return dataclasses.replace(self, **kwargs)

    def is_track_completed(self, track_id: str) -> bool:
        t = self.tracks.get(track_id)
        return t is not None and t.status == "completed"

    def all_tracks_completed(self) -> bool:
        if not self.pipeline_order:
            return False
        return all(self.is_track_completed(t) for t in self.pipeline_order)

    @staticmethod
    def extract_stage(qualified_track: str) -> str:
        """从 qualified track id（如 dev.backend）中提取 stage 名。"""
        parts = qualified_track.rsplit(".", 1)
        return parts[0] if len(parts) > 1 else ""