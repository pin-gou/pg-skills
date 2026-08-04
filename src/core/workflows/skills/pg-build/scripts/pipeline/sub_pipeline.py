"""SubPipeline — 递归子 pipeline 容器.

Fix 循环 / gate-fix 循环通过创建 SubPipeline 实现递归复用 reducer。
不需要状态 flag（如 `in_fix_cycle` / `fix_cycles` / `cycles_remaining`）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# 子 pipeline 类型常量
FIX_CYCLE = "fix-cycle"       # verify escalate → fix → verify
GATE_FIX_CYCLE = "gate-fix-cycle"  # gate fail → fix-gate → verify → gate
REVIEW_CYCLE = "review-cycle"  # review escalate → fix-review → review
# v3.5: scenario track 专用子 pipeline
# scenario-execute escalate → scenario-fix → scenario-execute(重跑)
SCENARIO_FIX_CYCLE = "scenario-fix-cycle"

# 各子 pipeline 类型的 phase 序列
FIX_CYCLE_PHASES: tuple[str, ...] = ("fix", "verify")
GATE_FIX_CYCLE_PHASES: tuple[str, ...] = ("fix-gate", "verify", "gate")
REVIEW_CYCLE_PHASES: tuple[str, ...] = ("fix-review", "review")
SCENARIO_FIX_CYCLE_PHASES: tuple[str, ...] = ("scenario-fix",)


@dataclass(frozen=True)
class SubPipeline:
    """递归子 pipeline。

    主 pipeline 的 reducer 检测到 verify escalate 或 gate fail 时，
    创建一个 SubPipeline 实例。子 pipeline 的推进复用了主 pipeline
    的 reducer（相同的 match 逻辑），区别在于：
      - 子 pipeline 的 phase 序列走完即完成
      - 子 pipeline 完成后触发主 pipeline 的对应 phase 推进

    P0-A 字段（v2.7 起）：
      - parent_report_path: 父 phase 产出报告的绝对路径，用于 dispatch 的
        {verify_report_path} / {code_view_report_path} 占位符注入
      - escalation_reason: 触发 escalate / fail 的父 phase summary，
        用于 dispatch 的 {reason} 占位符
      - failed_v_tasks: 父 verify phase 中标记为 FAIL 的 V-* ID 列表，
        用于 fix dispatch 的任务清单
      - created_at: 子 pipeline 创建时间（ISO 8601），用于 {failed_at}
    """

    pipeline_id: str            # e.g. "dev.backend.fix-1"
    parent_track: str           # e.g. "dev.backend"
    parent_phase: str           # "verify" | "gate"
    cycle: int                  # 1-based
    kind: str                   # FIX_CYCLE | GATE_FIX_CYCLE
    phases: tuple[str, ...]     # phase 序列
    current_index: int = 0      # 当前在 phases 中的下标
    status: str = "pending"     # pending | running | completed
    # === P0-A 新增字段（旧快照 missing 时默认空值，向后兼容） ===
    parent_report_path: str = ""
    escalation_reason: str = ""
    failed_v_tasks: tuple[str, ...] = ()
    created_at: str = ""

    def advance(self) -> "SubPipeline":
        """将 current_index 推进到下一 phase。返回新的 SubPipeline（不可变）。"""
        next_idx = self.current_index + 1
        if next_idx >= len(self.phases):
            return SubPipeline(
                pipeline_id=self.pipeline_id,
                parent_track=self.parent_track,
                parent_phase=self.parent_phase,
                cycle=self.cycle,
                kind=self.kind,
                phases=self.phases,
                current_index=self.current_index,
                status="completed",
            )
        return SubPipeline(
            pipeline_id=self.pipeline_id,
            parent_track=self.parent_track,
            parent_phase=self.parent_phase,
            cycle=self.cycle,
            kind=self.kind,
            phases=self.phases,
            current_index=next_idx,
            status="running",
        )

    @property
    def current_phase(self) -> str:
        """当前正在执行的 phase。"""
        if self.current_index < len(self.phases):
            return self.phases[self.current_index]
        return ""

    @property
    def is_last_phase(self) -> bool:
        """当前是否是最后一 phase（执行完就回到主 pipeline）。"""
        return self.current_index >= len(self.phases) - 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "parent_track": self.parent_track,
            "parent_phase": self.parent_phase,
            "cycle": self.cycle,
            "kind": self.kind,
            "phases": list(self.phases),
            "current_index": self.current_index,
            "status": self.status,
            # === P0-A 新增字段 ===
            "parent_report_path": self.parent_report_path,
            "escalation_reason": self.escalation_reason,
            "failed_v_tasks": list(self.failed_v_tasks),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SubPipeline":
        return cls(
            pipeline_id=d["pipeline_id"],
            parent_track=d["parent_track"],
            parent_phase=d["parent_phase"],
            cycle=d["cycle"],
            kind=d.get("kind", FIX_CYCLE),
            phases=tuple(d.get("phases", ())),
            current_index=d.get("current_index", 0),
            status=d.get("status", "pending"),
            # === P0-A 新增字段，缺失时走 default 保持向后兼容 ===
            parent_report_path=d.get("parent_report_path", ""),
            escalation_reason=d.get("escalation_reason", ""),
            failed_v_tasks=tuple(d.get("failed_v_tasks", ())),
            created_at=d.get("created_at", ""),
        )

    @staticmethod
    def build_id(track: str, kind: str, cycle: int) -> str:
        """生成子 pipeline 的全局 ID。"""
        return f"{track}.{kind}-{cycle}"


def create_fix_cycle(
    track: str,
    cycle: int,
    *,
    parent_report_path: str = "",
    escalation_reason: str = "",
    failed_v_tasks: tuple[str, ...] | list[str] = (),
    created_at: str = "",
) -> SubPipeline:
    """创建 fix 子 pipeline（verify escalate 时调用）。

    P0-A 新增参数（v2.7 起）：
      - parent_report_path: verify 报告的绝对路径，注入到 fix dispatch 的
        {verify_report_path} 占位符
      - escalation_reason: verify escalate 的 summary，注入到 {reason}
      - failed_v_tasks: verify 中标记 FAIL 的 V-* ID 列表，
        注入到 fix dispatch 的任务清单
      - created_at: ISO 8601 时间戳，注入到 {failed_at}
    """
    return SubPipeline(
        pipeline_id=SubPipeline.build_id(track, FIX_CYCLE, cycle),
        parent_track=track,
        parent_phase="verify",
        cycle=cycle,
        kind=FIX_CYCLE,
        phases=FIX_CYCLE_PHASES,
        current_index=0,
        status="running",
        parent_report_path=parent_report_path,
        escalation_reason=escalation_reason,
        failed_v_tasks=tuple(failed_v_tasks),
        created_at=created_at,
    )


def create_gate_fix_cycle(
    track: str,
    cycle: int,
    *,
    parent_report_path: str = "",
    escalation_reason: str = "",
    failed_v_tasks: tuple[str, ...] | list[str] = (),
    created_at: str = "",
) -> SubPipeline:
    """创建 gate-fix 子 pipeline（gate fail 时调用）。

    P0-A 新增参数：同 create_fix_cycle。
    """
    return SubPipeline(
        pipeline_id=SubPipeline.build_id(track, GATE_FIX_CYCLE, cycle),
        parent_track=track,
        parent_phase="gate",
        cycle=cycle,
        kind=GATE_FIX_CYCLE,
        phases=GATE_FIX_CYCLE_PHASES,
        current_index=0,
        status="running",
        parent_report_path=parent_report_path,
        escalation_reason=escalation_reason,
        failed_v_tasks=tuple(failed_v_tasks),
        created_at=created_at,
    )


def create_review_cycle(
    track: str,
    cycle: int,
    *,
    parent_report_path: str = "",
    escalation_reason: str = "",
    failed_v_tasks: tuple[str, ...] | list[str] = (),
    created_at: str = "",
) -> SubPipeline:
    """创建 review 子 pipeline（review escalate 时调用）。

    v2.6 新增：与 verify→fix 循环解耦，独立计数 review_fix_cycles。
    v2.7 起携带父 report 路径 / escalation_reason / 失败 R-*（或 V-*）list。
    """
    return SubPipeline(
        pipeline_id=SubPipeline.build_id(track, REVIEW_CYCLE, cycle),
        parent_track=track,
        parent_phase="review",
        cycle=cycle,
        kind=REVIEW_CYCLE,
        phases=REVIEW_CYCLE_PHASES,
        current_index=0,
        status="running",
        parent_report_path=parent_report_path,
        escalation_reason=escalation_reason,
        failed_v_tasks=tuple(failed_v_tasks),
        created_at=created_at,
    )


def create_scenario_fix_cycle(
    track: str,
    cycle: int,
    *,
    parent_report_path: str = "",
    escalation_reason: str = "",
    failed_scenarios: tuple[str, ...] | list[str] = (),
    created_at: str = "",
) -> SubPipeline:
    """v3.5 新增：scenario-fix 子 pipeline（scenario-execute escalate 时调用）。

    唯一区别于其他 fix 子 pipeline：
      - parent_phase = "scenario-execute"（不是 verify/review/gate）
      - phases = ("scenario-fix",) 单元素
      - 子 pipeline 完成后由主 reducer 触发 scenario-execute 重跑

    参数:
      failed_scenarios: 失败的 scenario_id 列表，存入 failed_v_tasks 复用字段
                        （reducer 读取时区分 parent_phase 即可）
    """
    return SubPipeline(
        pipeline_id=SubPipeline.build_id(track, SCENARIO_FIX_CYCLE, cycle),
        parent_track=track,
        parent_phase="scenario-execute",
        cycle=cycle,
        kind=SCENARIO_FIX_CYCLE,
        phases=SCENARIO_FIX_CYCLE_PHASES,
        current_index=0,
        status="running",
        parent_report_path=parent_report_path,
        escalation_reason=escalation_reason,
        failed_v_tasks=tuple(failed_scenarios),
        created_at=created_at,
    )