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
CODE_VIEW_CYCLE = "code-view-cycle"  # code-view escalate → fix-code-view → code-view

# 各子 pipeline 类型的 phase 序列
FIX_CYCLE_PHASES: tuple[str, ...] = ("fix", "verify")
GATE_FIX_CYCLE_PHASES: tuple[str, ...] = ("fix-gate", "verify", "gate")
CODE_VIEW_CYCLE_PHASES: tuple[str, ...] = ("fix-code-view", "code-view")


@dataclass(frozen=True)
class SubPipeline:
    """递归子 pipeline。

    主 pipeline 的 reducer 检测到 verify escalate 或 gate fail 时，
    创建一个 SubPipeline 实例。子 pipeline 的推进复用了主 pipeline
    的 reducer（相同的 match 逻辑），区别在于：
      - 子 pipeline 的 phase 序列走完即完成
      - 子 pipeline 完成后触发主 pipeline 的对应 phase 推进
    """

    pipeline_id: str            # e.g. "dev.backend.fix-1"
    parent_track: str           # e.g. "dev.backend"
    parent_phase: str           # "verify" | "gate"
    cycle: int                  # 1-based
    kind: str                   # FIX_CYCLE | GATE_FIX_CYCLE
    phases: tuple[str, ...]     # phase 序列
    current_index: int = 0      # 当前在 phases 中的下标
    status: str = "pending"     # pending | running | completed

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
        )

    @staticmethod
    def build_id(track: str, kind: str, cycle: int) -> str:
        """生成子 pipeline 的全局 ID。"""
        return f"{track}.{kind}-{cycle}"


def create_fix_cycle(track: str, cycle: int) -> SubPipeline:
    """创建 fix 子 pipeline（verify escalate 时调用）。"""
    return SubPipeline(
        pipeline_id=SubPipeline.build_id(track, FIX_CYCLE, cycle),
        parent_track=track,
        parent_phase="verify",
        cycle=cycle,
        kind=FIX_CYCLE,
        phases=FIX_CYCLE_PHASES,
        current_index=0,
        status="running",
    )


def create_gate_fix_cycle(track: str, cycle: int) -> SubPipeline:
    """创建 gate-fix 子 pipeline（gate fail 时调用）。"""
    return SubPipeline(
        pipeline_id=SubPipeline.build_id(track, GATE_FIX_CYCLE, cycle),
        parent_track=track,
        parent_phase="gate",
        cycle=cycle,
        kind=GATE_FIX_CYCLE,
        phases=GATE_FIX_CYCLE_PHASES,
        current_index=0,
        status="running",
    )


def create_code_view_cycle(track: str, cycle: int) -> SubPipeline:
    """创建 code-view 子 pipeline（code-view escalate 时调用）。

    v2.6 新增：与 verify→fix 循环解耦，独立计数 code_view_fix_cycles。
    """
    return SubPipeline(
        pipeline_id=SubPipeline.build_id(track, CODE_VIEW_CYCLE, cycle),
        parent_track=track,
        parent_phase="code-view",
        cycle=cycle,
        kind=CODE_VIEW_CYCLE,
        phases=CODE_VIEW_CYCLE_PHASES,
        current_index=0,
        status="running",
    )