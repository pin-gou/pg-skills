"""Tests for scenario track lifecycle.

v3.x 重构: scenario-prepare 已删除, 由 restart_all_instances env-action 替代.
scenario track 直接 dispatch scenario-execute, detect 在 dispatch 前检查
stage_restarted 确保环境是最新一次构建.

测试范围:
1. state.py 常量 (SUB_SCENARIO_EXECUTE / SUB_SCENARIO_FIX / SCENARIO_PHASES)
2. events.py 状态矩阵 (PHASE_STATUS_ALLOWED)
3. sub_pipeline.py: create_scenario_fix_cycle
4. reducer: scenario-execute (completed / escalate / failed)
5. reducer: scenario-fix (子 pipeline 完成后回到 execute)
6. detect.py: scenario track 路由 (含 restart 前置检查)
7. dispatch.py: PHASE_AGENTS + ALLOWED_STATUSES (scenario-prepare 已删除)
8. sub_agent_contract: PHASE_RULES (scenario-prepare 已删除)
"""
from __future__ import annotations

import os
import unittest

from pipeline.events import (
    PipelineRecord,
    PHASE_STATUS_ALLOWED,
    EVT_SCENARIO_CYCLE_STARTED,
    EVT_SCENARIO_TRACK_COMPLETED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_ESCALATE,
)
from pipeline.reducer import reduce_state
from pipeline.state import (
    PipelineState,
    TrackState,
    PhaseState,
    SUB_SCENARIO_EXECUTE,
    SUB_SCENARIO_FIX,
    SCENARIO_FIX_CYCLE_PHASES,
    SCENARIO_PHASES,
)
from pipeline.sub_pipeline import (
    create_scenario_fix_cycle,
    SCENARIO_FIX_CYCLE,
)
from pipeline import detect as detect_mod
from pipeline import dispatch as dispatch_mod
from pipeline.sub_agent_contract import PHASE_RULES


# ============================================================
# 工具函数
# ============================================================

def _make_scenario_track(
    track_id: str = "real-integration.scenario-test",
    max_fix_retries: int = 5,
    max_fail_retries: int = 3,
) -> TrackState:
    """构造一个 scenario track 的最小 TrackState。"""
    return TrackState(
        track_id=track_id,
        bare="scenario-test",
        label="scenario-test",
        modules=("backend", "frontend", "agent"),
        max_fix_retries=max_fix_retries,
        max_fail_retries=max_fail_retries,
    )


def _make_state(
    track_id: str = "real-integration.scenario-test",
    track_type: str = "scenario",
    max_fix_retries: int = 5,
    env_name: str = "dev-local",
    scenario_last_restart_attempt: int = -1,
    current_stage: str = "",
    stage_order: tuple = (),
    stage_env_map: dict | None = None,
    stage_env_timeout: dict | None = None,
    scenario_max_fix_cycles: int | None = None,
) -> PipelineState:
    """构造一个 PipelineState 含一个 scenario track。

    v3.12: stage_restarted 已移除, 改为 per-track scenario_last_restart_attempt.
    detect 在 dispatch scenario-execute 前用 (attempt > scenario_last_restart_attempt)
    判定是否需要 restart.
    """
    t = TrackState(
        track_id=track_id,
        bare="scenario-test",
        label="scenario-test",
        modules=("backend", "frontend", "agent"),
        max_fix_retries=max_fix_retries,
        max_fail_retries=3,
        env_name=env_name,
        scenario_max_fix_cycles=scenario_max_fix_cycles,
        scenario_last_restart_attempt=scenario_last_restart_attempt,
    )
    return PipelineState(
        change="test-change",
        pipeline_order=(track_id,),
        track_types={track_id: track_type},
        tracks={track_id: t},
        status="running",
        current_track=track_id,
        current_phase="",
        stage_order=stage_order,
        stage_env_map=stage_env_map or {},
        stage_env_timeout=stage_env_timeout or {},
        current_stage=current_stage,
    )


# ============================================================
# 1. state.py 常量
# ============================================================

class TestStateScenarioPhases(unittest.TestCase):
    def test_sub_scenario_phase_constants(self):
        self.assertEqual(SUB_SCENARIO_EXECUTE, "scenario-execute")
        self.assertEqual(SUB_SCENARIO_FIX, "scenario-fix")

    def test_scenario_phases_tuple_excludes_prepare(self):
        # v3.x: scenario-prepare 已删除
        self.assertEqual(SCENARIO_PHASES, ("scenario-execute",))
        self.assertNotIn("scenario-prepare", SCENARIO_PHASES)

    def test_scenario_fix_cycle_phases(self):
        self.assertEqual(SCENARIO_FIX_CYCLE_PHASES, ("scenario-fix",))


# ============================================================
# 2. events.py 状态矩阵 + event types
# ============================================================

class TestEventScenarioPhases(unittest.TestCase):
    def test_phase_status_allowed_covers_scenario(self):
        self.assertIn(SUB_SCENARIO_EXECUTE, PHASE_STATUS_ALLOWED)
        self.assertIn(SUB_SCENARIO_FIX, PHASE_STATUS_ALLOWED)
        self.assertNotIn("scenario-prepare", PHASE_STATUS_ALLOWED)
        self.assertEqual(
            PHASE_STATUS_ALLOWED[SUB_SCENARIO_FIX],
            frozenset({STATUS_COMPLETED, STATUS_FAILED}),
        )
        # execute 多 escalate
        self.assertIn(STATUS_ESCALATE, PHASE_STATUS_ALLOWED[SUB_SCENARIO_EXECUTE])

    def test_scenario_event_types_defined(self):
        self.assertEqual(EVT_SCENARIO_CYCLE_STARTED, "scenario_cycle_started")
        self.assertEqual(EVT_SCENARIO_TRACK_COMPLETED, "scenario_track_completed")


# ============================================================
# 3. sub_pipeline.py: create_scenario_fix_cycle
# ============================================================

class TestScenarioFixCycle(unittest.TestCase):
    def test_create_scenario_fix_cycle_basic(self):
        sp = create_scenario_fix_cycle(
            "real-integration.scenario-test", cycle=1,
            parent_report_path="/tmp/foo.md",
            escalation_reason="S-test failed",
            failed_scenarios=("S-test",),
            created_at="2026-07-14T10:00:00+08:00",
        )
        self.assertEqual(sp.parent_track, "real-integration.scenario-test")
        self.assertEqual(sp.parent_phase, "scenario-execute")
        self.assertEqual(sp.kind, SCENARIO_FIX_CYCLE)
        self.assertEqual(sp.cycle, 1)
        self.assertEqual(sp.phases, ("scenario-fix",))
        self.assertEqual(sp.current_index, 0)
        self.assertEqual(sp.status, "running")
        self.assertEqual(sp.parent_report_path, "/tmp/foo.md")
        self.assertEqual(sp.failed_v_tasks, ("S-test",))

    def test_scenario_fix_cycle_advance_to_last_phase(self):
        """SCENARIO_FIX_CYCLE 只有 1 phase，当前 index=0 即 is_last_phase=True。"""
        sp = create_scenario_fix_cycle("t", cycle=1)
        self.assertTrue(sp.is_last_phase)
        advanced = sp.advance()
        # advance 在 is_last_phase 时返回 status=completed 但 current_index 不变
        self.assertEqual(advanced.status, "completed")
        self.assertEqual(advanced.current_index, 0)


# ============================================================
# 4. reducer: scenario-execute
# ============================================================

class TestScenarioExecute(unittest.TestCase):
    def test_execute_completed_track_done(self):
        state = _make_state()
        t = state.tracks["real-integration.scenario-test"]
        t = t.replace(
            phases={
                SUB_SCENARIO_EXECUTE: PhaseState(status="running", attempt=1),
            },
        )
        state = state.replace(
            tracks={**state.tracks, "real-integration.scenario-test": t},
            current_phase=SUB_SCENARIO_EXECUTE,
        )
        record = PipelineRecord(
            track="real-integration.scenario-test",
            phase=SUB_SCENARIO_EXECUTE,
            failed_scenarios="[]",
            skipped_scenarios="[]",
            status=STATUS_COMPLETED,
            summary="all scenarios passed",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "advance")
        self.assertEqual(
            new_state.tracks["real-integration.scenario-test"].status,
            "completed",
        )
        self.assertEqual(new_state.current_track, "")

    def test_execute_escalate_first_creates_fix_subpipeline(self):
        state = _make_state(max_fix_retries=5)
        t = state.tracks["real-integration.scenario-test"]
        t = t.replace(
            phases={
                SUB_SCENARIO_EXECUTE: PhaseState(
                    status="running", attempt=1,
                    report_path="/tmp/exec.md",
                ),
            },
        )
        state = state.replace(
            tracks={**state.tracks, "real-integration.scenario-test": t},
            current_phase=SUB_SCENARIO_EXECUTE,
        )
        record = PipelineRecord(
            track="real-integration.scenario-test",
            phase=SUB_SCENARIO_EXECUTE,
            failed_scenarios="[\"S-create-vm\"]",
            skipped_scenarios="[]",
            status=STATUS_ESCALATE,
            summary="S-create-vm failed",
            tasks_updated=("S-create-vm",),
            report_path="/tmp/exec.md",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, SUB_SCENARIO_FIX)
        self.assertEqual(action.cycle, 1)
        sp = new_state.current_sub_pipeline
        self.assertIsNotNone(sp)
        self.assertEqual(sp.kind, SCENARIO_FIX_CYCLE)
        self.assertEqual(sp.parent_phase, "scenario-execute")
        self.assertEqual(sp.failed_v_tasks, ("S-create-vm",))
        fix_cycles = new_state.tracks[
            "real-integration.scenario-test"
        ].phases[SUB_SCENARIO_EXECUTE].fix_cycles
        self.assertEqual(len(fix_cycles), 1)

    def test_execute_escalate_no_tasks_updated_is_error(self):
        state = _make_state()
        t = state.tracks["real-integration.scenario-test"]
        t = t.replace(
            phases={
                SUB_SCENARIO_EXECUTE: PhaseState(status="running", attempt=1),
            },
        )
        state = state.replace(
            tracks={**state.tracks, "real-integration.scenario-test": t},
            current_phase=SUB_SCENARIO_EXECUTE,
        )
        record = PipelineRecord(
            track="real-integration.scenario-test",
            phase=SUB_SCENARIO_EXECUTE,
            failed_scenarios='["S-x"]',
            skipped_scenarios="[]",
            status=STATUS_ESCALATE,
            summary="",
            tasks_updated=(),
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "error")
        self.assertIn("tasks_updated", action.detail["reason"])

    # ── v1.1.0 (P1-2) result 契约测试 ──

    def _execute_state(self) -> PipelineState:
        state = _make_state()
        t = state.tracks["real-integration.scenario-test"]
        t = t.replace(phases={
            SUB_SCENARIO_EXECUTE: PhaseState(status="running", attempt=1),
        })
        return state.replace(
            tracks={**state.tracks, "real-integration.scenario-test": t},
            current_phase=SUB_SCENARIO_EXECUTE,
        )

    def test_p1_2_missing_failed_scenarios_is_error(self):
        """escalate/completed 缺 failed_scenarios → schema_violation error。"""
        record = PipelineRecord(
            track="real-integration.scenario-test",
            phase=SUB_SCENARIO_EXECUTE,
            status=STATUS_ESCALATE,
            summary="x",
            tasks_updated=("S-x",),
            skipped_scenarios="[]",
        )
        _ns, action = reduce_state(self._execute_state(), record)
        self.assertEqual(action.kind, "error")
        self.assertIn("failed_scenarios", action.detail["reason"])

    def test_p1_2_missing_skipped_scenarios_is_error(self):
        record = PipelineRecord(
            track="real-integration.scenario-test",
            phase=SUB_SCENARIO_EXECUTE,
            status=STATUS_ESCALATE,
            summary="x",
            tasks_updated=("S-x",),
            failed_scenarios='["S-x"]',
        )
        _ns, action = reduce_state(self._execute_state(), record)
        self.assertEqual(action.kind, "error")
        self.assertIn("skipped_scenarios", action.detail["reason"])

    def test_p1_2_escalate_empty_failed_scenarios_is_error(self):
        """escalate 时 failed_scenarios 必须非空。"""
        record = PipelineRecord(
            track="real-integration.scenario-test",
            phase=SUB_SCENARIO_EXECUTE,
            status=STATUS_ESCALATE,
            summary="x",
            tasks_updated=("S-x",),
            failed_scenarios="[]",
            skipped_scenarios="[]",
        )
        _ns, action = reduce_state(self._execute_state(), record)
        self.assertEqual(action.kind, "error")
        self.assertIn("failed_scenarios 非空", action.detail["reason"])

    def test_p1_2_completed_with_failed_scenarios_is_error(self):
        """completed 时 failed_scenarios 必须为空。"""
        record = PipelineRecord(
            track="real-integration.scenario-test",
            phase=SUB_SCENARIO_EXECUTE,
            status=STATUS_COMPLETED,
            summary="x",
            failed_scenarios='["S-x"]',
            skipped_scenarios="[]",
        )
        _ns, action = reduce_state(self._execute_state(), record)
        self.assertEqual(action.kind, "error")
        self.assertIn("status=completed", action.detail["reason"])

    def test_p1_2_invalid_json_is_error(self):
        record = PipelineRecord(
            track="real-integration.scenario-test",
            phase=SUB_SCENARIO_EXECUTE,
            status=STATUS_ESCALATE,
            summary="x",
            tasks_updated=("S-x",),
            failed_scenarios="not-json",
            skipped_scenarios="[]",
        )
        _ns, action = reduce_state(self._execute_state(), record)
        self.assertEqual(action.kind, "error")
        self.assertIn("不是合法 JSON", action.detail["reason"])

    def test_p1_2_failed_status_skips_validation(self):
        """FAILED (sub-agent 崩溃) 不强制结构化清单，走 attempt 重试。"""
        record = PipelineRecord(
            track="real-integration.scenario-test",
            phase=SUB_SCENARIO_EXECUTE,
            status=STATUS_FAILED,
            summary="crash",
        )
        _ns, action = reduce_state(self._execute_state(), record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, SUB_SCENARIO_EXECUTE)

    def test_execute_escalate_exhausted_workflow_failed(self):
        """max_fix_retries 耗尽 → workflow_failed（不复用 accept_gap）。"""
        state = _make_state(max_fix_retries=3)
        existing_cycles = tuple(
            {"cycle": i, "status": "completed"} for i in range(1, 4)
        )
        t = state.tracks["real-integration.scenario-test"]
        t = t.replace(
            phases={
                SUB_SCENARIO_EXECUTE: PhaseState(
                    status="running", attempt=1,
                    fix_cycles=existing_cycles,
                ),
            },
        )
        state = state.replace(
            tracks={**state.tracks, "real-integration.scenario-test": t},
            current_phase=SUB_SCENARIO_EXECUTE,
        )
        record = PipelineRecord(
            track="real-integration.scenario-test",
            phase=SUB_SCENARIO_EXECUTE,
            failed_scenarios="[\"S-xyz\"]",
            skipped_scenarios="[]",
            status=STATUS_ESCALATE,
            summary="S-xyz failed again",
            tasks_updated=("S-xyz",),
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "workflow_failed")
        self.assertIn("exhausted", action.detail["reason"])

    def _state_with_cycles(self, n_cycles: int, max_fix: int,
                           scenario_max: int | None) -> PipelineState:
        """构造含 n_cycles 个已完成 fix cycle 的 state。"""
        state = _make_state(
            max_fix_retries=max_fix,
            scenario_max_fix_cycles=scenario_max,
        )
        existing = tuple({"cycle": i, "status": "completed"} for i in range(1, n_cycles + 1))
        t = state.tracks["real-integration.scenario-test"]
        t = t.replace(phases={
            SUB_SCENARIO_EXECUTE: PhaseState(status="running", attempt=1, fix_cycles=existing),
        })
        return state.replace(
            tracks={**state.tracks, "real-integration.scenario-test": t},
            current_phase=SUB_SCENARIO_EXECUTE,
        )

    def test_scenario_max_fix_cycles_overrides_max_fix_retries(self):
        """v1.1.0 (P1-4): scenario_max_fix_cycles 独立于 max_fix_retries。

        max_fix_retries=5 但 scenario_max_fix_cycles=2 → 第 2 次 escalate 即耗尽。
        """
        state = self._state_with_cycles(n_cycles=2, max_fix=5, scenario_max=2)
        record = PipelineRecord(
            track="real-integration.scenario-test",
            phase=SUB_SCENARIO_EXECUTE,
            failed_scenarios="[\"S-xyz\"]",
            skipped_scenarios="[]",
            status=STATUS_ESCALATE,
            summary="still failing",
            tasks_updated=("S-xyz",),
        )
        _new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "workflow_failed")
        self.assertIn("exhausted", action.detail["reason"])
        self.assertIn("2/2", action.detail["reason"])

    def test_scenario_max_fix_cycles_below_max_allows_more(self):
        """scenario_max_fix_cycles > max_fix_retries 时放宽 scenario 循环上限。

        max_fix_retries=2 但 scenario_max_fix_cycles=5 → 2 个 cycle 后仍可 escalate。
        """
        state = self._state_with_cycles(n_cycles=2, max_fix=2, scenario_max=5)
        record = PipelineRecord(
            track="real-integration.scenario-test",
            phase=SUB_SCENARIO_EXECUTE,
            failed_scenarios="[\"S-xyz\"]",
            skipped_scenarios="[]",
            status=STATUS_ESCALATE,
            summary="still failing",
            tasks_updated=("S-xyz",),
        )
        _new_state, action = reduce_state(state, record)
        # 未耗尽 → 创建新 scenario-fix 子 pipeline（dispatch）
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, SUB_SCENARIO_FIX)

    def test_scenario_max_fix_cycles_fallback_to_max_fix_retries(self):
        """scenario_max_fix_cycles=None 时 fallback 到 max_fix_retries。"""
        state = self._state_with_cycles(n_cycles=3, max_fix=3, scenario_max=None)
        record = PipelineRecord(
            track="real-integration.scenario-test",
            phase=SUB_SCENARIO_EXECUTE,
            failed_scenarios="[\"S-xyz\"]",
            skipped_scenarios="[]",
            status=STATUS_ESCALATE,
            summary="still failing",
            tasks_updated=("S-xyz",),
        )
        _new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "workflow_failed")
        self.assertIn("3/3", action.detail["reason"])

    def test_execute_failed_attempt_retry(self):
        state = _make_state()
        t = state.tracks["real-integration.scenario-test"]
        t = t.replace(
            phases={
                SUB_SCENARIO_EXECUTE: PhaseState(status="running", attempt=1),
            },
        )
        state = state.replace(
            tracks={**state.tracks, "real-integration.scenario-test": t},
            current_phase=SUB_SCENARIO_EXECUTE,
        )
        record = PipelineRecord(
            track="real-integration.scenario-test",
            phase=SUB_SCENARIO_EXECUTE,
            status=STATUS_FAILED,
            summary="sub-agent crash",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, SUB_SCENARIO_EXECUTE)
        self.assertEqual(action.attempt, 2)


# ============================================================
# 5. reducer: scenario-fix (子 pipeline 中的 scenario-fix)
# ============================================================

class TestScenarioFixHandler(unittest.TestCase):
    def _state_with_fix_subpipeline(
        self, fix_attempt: int = 1, exec_attempt: int = 0, max_fail_retries: int = 3,
    ) -> PipelineState:
        """构造一个活跃 scenario-fix 子 pipeline 的 state。"""
        sp = create_scenario_fix_cycle(
            "real-integration.scenario-test",
            cycle=fix_attempt,
            parent_report_path="/tmp/exec.md",
            failed_scenarios=("S-test",),
        )
        t = TrackState(
            track_id="real-integration.scenario-test",
            bare="scenario-test",
            modules=("backend",),
            max_fail_retries=max_fail_retries,
            phases={
                SUB_SCENARIO_EXECUTE: PhaseState(
                    status="running",
                    attempt=exec_attempt,
                    fix_cycles=(
                        {"cycle": fix_attempt, "status": "pending"},
                    ),
                ),
            },
        )
        return PipelineState(
            change="t",
            pipeline_order=("real-integration.scenario-test",),
            track_types={"real-integration.scenario-test": "scenario"},
            tracks={"real-integration.scenario-test": t},
            current_sub_pipeline=sp,
            current_track="real-integration.scenario-test",
            current_phase=SUB_SCENARIO_FIX,
        )

    def test_scenario_fix_completed_advances_to_execute(self):
        state = self._state_with_fix_subpipeline()
        record = PipelineRecord(
            track="real-integration.scenario-test",
            phase=SUB_SCENARIO_FIX,
            status=STATUS_COMPLETED,
            summary="fixed",
            tasks_updated=("S-test",),
        )
        new_state, action = reduce_state(state, record)
        # 子 pipeline phase 完成后 → 触发 scenario-execute 重跑
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, SUB_SCENARIO_EXECUTE)
        self.assertIsNone(new_state.current_sub_pipeline)
        # v1.1.0 (P1-5): attempt 从 0 递增到 1
        self.assertEqual(action.attempt, 1)
        fix_cycles = new_state.tracks[
            "real-integration.scenario-test"
        ].phases[SUB_SCENARIO_EXECUTE].fix_cycles
        self.assertEqual(fix_cycles[-1]["status"], "completed")
        self.assertEqual(
            new_state.tracks["real-integration.scenario-test"].phases[SUB_SCENARIO_EXECUTE].attempt, 1,
        )

    def test_scenario_fix_failed_still_advances_to_execute(self):
        """scenario-fix 失败也回到 execute（由 max_fix_retries 控制循环）。"""
        state = self._state_with_fix_subpipeline()
        record = PipelineRecord(
            track="real-integration.scenario-test",
            phase=SUB_SCENARIO_FIX,
            status=STATUS_FAILED,
            summary="could not locate root cause",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, SUB_SCENARIO_EXECUTE)
        fix_cycles = new_state.tracks[
            "real-integration.scenario-test"
        ].phases[SUB_SCENARIO_EXECUTE].fix_cycles
        self.assertEqual(fix_cycles[-1]["status"], "failed")

    def test_attempt_increments_across_fix_cycles(self):
        """v1.1.0 (P1-5): attempt 跨 fix 循环递增。"""
        state = self._state_with_fix_subpipeline(exec_attempt=2)
        record = PipelineRecord(
            track="real-integration.scenario-test",
            phase=SUB_SCENARIO_FIX,
            status=STATUS_COMPLETED,
            summary="fixed",
            tasks_updated=("S-test",),
        )
        _new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.attempt, 3)

    def test_attempt_exceeds_max_fail_retries_workflow_failed(self):
        """v1.1.0 (P1-5): attempt 超 max_fail_retries → workflow_failed。"""
        state = self._state_with_fix_subpipeline(exec_attempt=3, max_fail_retries=3)
        record = PipelineRecord(
            track="real-integration.scenario-test",
            phase=SUB_SCENARIO_FIX,
            status=STATUS_COMPLETED,
            summary="fixed",
            tasks_updated=("S-test",),
        )
        _new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "workflow_failed")
        self.assertIn("max_fail_retries", action.detail["reason"])


# ============================================================
# 6. detect.py: scenario track 路由 + restart 前置检查
# ============================================================

class TestDetectScenarioAction(unittest.TestCase):
    def test_detect_initial_dispatches_restart_then_execute(self):
        """v3.x: scenario track 首次 dispatch 应先 env_switch[restart], 再 execute.

        注意: 实际场景里 stage_prepared 必先有值 (prepare_env 已跑过).
        测试中用 track_id 格式 "integration.scenario-test" 让 extract_stage 返回 "integration".
        """
        state = _make_state(
            track_id="integration.scenario-test",
            stage_order=("integration",),
            current_stage="integration",
            stage_env_map={"integration": "dev-3tier"},
            stage_env_timeout={"dev-3tier": 600},
            scenario_last_restart_attempt=-1,
        )
        # 模拟 prepare_env 已完成 (stage_prepared 含 "integration")
        state = state.replace(stage_prepared={"integration"})
        action = detect_mod.next_pending(state)
        self.assertEqual(action.kind, "env_switch")
        self.assertEqual(action.phase, "restart")
        self.assertEqual(action.track, "integration.scenario-test")
        self.assertEqual(action.detail["stage"], "integration")
        self.assertEqual(action.detail["env_name"], "dev-3tier")

    def test_detect_after_restart_dispatches_execute(self):
        """v3.12: scenario_last_restart_attempt 已对齐 attempt 时, dispatch scenario-execute."""
        state = _make_state(
            track_id="integration.scenario-test",
            stage_order=("integration",),
            current_stage="integration",
            stage_env_map={"integration": "dev-3tier"},
            scenario_last_restart_attempt=0,
        )
        state = state.replace(stage_prepared={"integration"})
        action = detect_mod.next_pending(state)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, SUB_SCENARIO_EXECUTE)
        self.assertEqual(action.track, "integration.scenario-test")

    def test_detect_no_stage_info_dispatches_execute_directly(self):
        """无 stage_order 时 (向后兼容旧 state), 直接 dispatch execute."""
        state = _make_state()  # 默认 stage_order=()
        action = detect_mod.next_pending(state)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, SUB_SCENARIO_EXECUTE)

    def test_detect_sub_pipeline_routes_to_scenario_fix(self):
        sp = create_scenario_fix_cycle("real-integration.scenario-test", cycle=2)
        t = TrackState(
            track_id="real-integration.scenario-test",
            bare="scenario-test",
        )
        state = PipelineState(
            change="t",
            pipeline_order=("real-integration.scenario-test",),
            track_types={"real-integration.scenario-test": "scenario"},
            tracks={"real-integration.scenario-test": t},
            current_sub_pipeline=sp,
        )
        action = detect_mod.next_pending(state)
        # 主入口对所有活跃子 pipeline 都路由到子 current_phase
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, SUB_SCENARIO_FIX)
        self.assertEqual(action.cycle, 2)


# ============================================================
# 7. dispatch.py: PHASE_AGENTS + ALLOWED_STATUSES (scenario-prepare 已删除)
# ============================================================

class TestDispatchScenario(unittest.TestCase):
    def test_phase_agents_excludes_scenario_prepare(self):
        """v3.x: scenario-prepare agent 已删除."""
        self.assertNotIn("scenario-prepare", dispatch_mod.PHASE_AGENTS)
        self.assertEqual(
            dispatch_mod.PHASE_AGENTS.get(SUB_SCENARIO_EXECUTE),
            "pg-build/scenario-execute",
        )
        self.assertEqual(
            dispatch_mod.PHASE_AGENTS.get(SUB_SCENARIO_FIX),
            "pg-build/scenario-fix",
        )

    def test_phase_allowed_statuses_excludes_scenario_prepare(self):
        self.assertNotIn("scenario-prepare", dispatch_mod.PHASE_ALLOWED_STATUSES)
        self.assertIn(SUB_SCENARIO_EXECUTE, dispatch_mod.PHASE_ALLOWED_STATUSES)
        self.assertIn(SUB_SCENARIO_FIX, dispatch_mod.PHASE_ALLOWED_STATUSES)
        # execute 含 escalate
        self.assertIn("escalate", dispatch_mod.PHASE_ALLOWED_STATUSES[SUB_SCENARIO_EXECUTE])

    def test_read_scenario_yaml_empty_when_missing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            result = dispatch_mod._read_scenario_yaml(tmp, "scenario-test.yaml")
            self.assertEqual(result, "")

    def test_read_scenario_yaml_returns_content(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = os.path.join(tmp, "scenario-test.yaml")
            with open(yaml_path, "w", encoding="utf-8") as f:
                f.write("scenarios:\n  - scenario_id: S-test\n")
            result = dispatch_mod._read_scenario_yaml(tmp, "scenario-test.yaml")
            self.assertIn("S-test", result)

    def test_read_scenario_yaml_default_filename(self):
        """默认 filename=scenario.yaml 兼容旧 change。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = os.path.join(tmp, "scenario.yaml")
            with open(yaml_path, "w", encoding="utf-8") as f:
                f.write("scenarios:\n  - scenario_id: S-legacy\n")
            result = dispatch_mod._read_scenario_yaml(tmp)
            self.assertIn("S-legacy", result)


# ============================================================
# 8. sub_agent_contract: PHASE_RULES (scenario-prepare 已删除)
# ============================================================

class TestSubAgentContractScenario(unittest.TestCase):
    def test_phase_rules_exclude_scenario_prepare(self):
        self.assertNotIn("scenario-prepare", PHASE_RULES)
        self.assertIn(SUB_SCENARIO_EXECUTE, PHASE_RULES)
        self.assertIn(SUB_SCENARIO_FIX, PHASE_RULES)

    def test_scenario_execute_escalate_only_tasks(self):
        rule = PHASE_RULES[SUB_SCENARIO_EXECUTE]
        self.assertEqual(rule["tasks_updated_required"], "escalate_only")


# ============================================================
# 9. orchestrator-level: 完整 execute → fix → execute → completed
# ============================================================

class TestScenarioTrackEnd2End(unittest.TestCase):
    def test_full_lifecycle_no_failure(self):
        """完整路径：execute.completed → track 完成。"""
        state = _make_state()
        new_state, action = reduce_state(
            state,
            PipelineRecord(
                track="real-integration.scenario-test",
                phase=SUB_SCENARIO_EXECUTE,
                failed_scenarios="[]",
                skipped_scenarios="[]",
                status=STATUS_COMPLETED,
                summary="all scenarios passed",
            ),
        )
        self.assertEqual(action.kind, "advance")
        self.assertEqual(
            new_state.tracks["real-integration.scenario-test"].status,
            "completed",
        )

    def test_full_lifecycle_with_one_fix_cycle(self):
        """完整路径：execute.escalate → fix.completed → execute.completed。"""
        state = _make_state()

        # Step 1: execute.escalate
        state, action = reduce_state(
            state,
            PipelineRecord(
                track="real-integration.scenario-test",
                phase=SUB_SCENARIO_EXECUTE,
                failed_scenarios="[\"S-fail\"]",
                skipped_scenarios="[]",
                status=STATUS_ESCALATE,
                tasks_updated=("S-fail",),
                summary="S-fail failed",
            ),
        )
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, SUB_SCENARIO_FIX)

        # Step 2: fix.completed → 应回到 execute
        state, action = reduce_state(
            state,
            PipelineRecord(
                track="real-integration.scenario-test",
                phase=SUB_SCENARIO_FIX,
                status=STATUS_COMPLETED,
                tasks_updated=("S-fail",),
                summary="fixed",
            ),
        )
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, SUB_SCENARIO_EXECUTE)
        self.assertIsNone(state.current_sub_pipeline)

        # Step 3: execute.completed → track 完成
        new_state, action = reduce_state(
            state,
            PipelineRecord(
                track="real-integration.scenario-test",
                phase=SUB_SCENARIO_EXECUTE,
                failed_scenarios="[]",
                skipped_scenarios="[]",
                status=STATUS_COMPLETED,
                summary="all passed after fix",
            ),
        )
        self.assertEqual(action.kind, "advance")
        self.assertEqual(
            new_state.tracks["real-integration.scenario-test"].status,
            "completed",
        )
        # fix_cycles 应累计到 1（escalate 触发 +1）
        fix_cycles = new_state.tracks[
            "real-integration.scenario-test"
        ].phases[SUB_SCENARIO_EXECUTE].fix_cycles
        self.assertEqual(len(fix_cycles), 1)

    def test_full_lifecycle_exhausted_workflow_failed(self):
        """完整路径：execute 连续 escalate 直到耗尽 max_fix_retries=1。"""
        state = _make_state(max_fix_retries=1)

        # Step 1-2: 第一次 escalate + fix（这是允许的 1 次 fix cycle）
        state, _ = reduce_state(
            state,
            PipelineRecord(
                track="real-integration.scenario-test",
                phase=SUB_SCENARIO_EXECUTE,
                failed_scenarios="[\"S-1\"]",
                skipped_scenarios="[]",
                status=STATUS_ESCALATE,
                tasks_updated=("S-1",),
            ),
        )
        state, _ = reduce_state(
            state,
            PipelineRecord(
                track="real-integration.scenario-test",
                phase=SUB_SCENARIO_FIX,
                status=STATUS_COMPLETED,
                tasks_updated=("S-1",),
            ),
        )

        # Step 3: 第二次 escalate → max_fix_retries=1 已耗尽 → workflow_failed
        new_state, action = reduce_state(
            state,
            PipelineRecord(
                track="real-integration.scenario-test",
                phase=SUB_SCENARIO_EXECUTE,
                failed_scenarios="[\"S-2\"]",
                skipped_scenarios="[]",
                status=STATUS_ESCALATE,
                tasks_updated=("S-2",),
            ),
        )
        self.assertEqual(action.kind, "workflow_failed")
        self.assertIn("exhausted", action.detail["reason"])


if __name__ == "__main__":
    unittest.main()