"""scenario track 端到端测试（v3.5）。

覆盖：
- state.py: SUB_SCENARIO_* 常量 + SCENARIO_FIX_CYCLE_PHASES
- events.py: PHASE_STATUS_ALLOWED / EVT_SCENARIO_CYCLE_STARTED
- sub_pipeline.py: create_scenario_fix_cycle + 推进
- reducer.py: _handle_scenario_prepare / _handle_scenario_execute /
              _handle_scenario_fix 全分支（完成 / escalate / fix exhausted / failed）
- detect.py: _detect_scenario_action 三阶段路由
- dispatch.py: PHASE_AGENTS 含 scenario-* + extract_scenario_md
- sub_agent_contract.py: PHASE_RULES 含 scenario-*
- orchestrator 集成：bootstrap → prepare → execute → (fix → execute)* → completed
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.events import (
    PipelineRecord,
    PipelineAction,
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
    SUB_SCENARIO_PREPARE,
    SUB_SCENARIO_EXECUTE,
    SUB_SCENARIO_FIX,
    SCENARIO_FIX_CYCLE_PHASES,
    SCENARIO_PHASES,
)
from pipeline.sub_pipeline import (
    SubPipeline,
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
    prepare_status: str = "completed",
    env_name: str = "dev-local",
) -> PipelineState:
    """构造一个 PipelineState 含一个 scenario track。"""
    t = TrackState(
        track_id=track_id,
        bare="scenario-test",
        label="scenario-test",
        modules=("backend", "frontend", "agent"),
        max_fix_retries=max_fix_retries,
        max_fail_retries=3,
        prepare_status=prepare_status,
        env_name=env_name,
    )
    return PipelineState(
        change="test-change",
        pipeline_order=(track_id,),
        track_types={track_id: track_type},
        tracks={track_id: t},
        status="running",
        current_track=track_id,
    )


# ============================================================
# 1. state.py 常量
# ============================================================

class TestStateScenarioPhases(unittest.TestCase):
    def test_sub_scenario_phase_constants(self):
        self.assertEqual(SUB_SCENARIO_PREPARE, "scenario-prepare")
        self.assertEqual(SUB_SCENARIO_EXECUTE, "scenario-execute")
        self.assertEqual(SUB_SCENARIO_FIX, "scenario-fix")

    def test_scenario_phases_tuple(self):
        self.assertEqual(SCENARIO_PHASES, ("scenario-prepare", "scenario-execute"))

    def test_scenario_fix_cycle_phases(self):
        self.assertEqual(SCENARIO_FIX_CYCLE_PHASES, ("scenario-fix",))


# ============================================================
# 2. events.py 状态矩阵 + event types
# ============================================================

class TestEventScenarioPhases(unittest.TestCase):
    def test_phase_status_allowed_covers_scenario(self):
        self.assertIn(SUB_SCENARIO_PREPARE, PHASE_STATUS_ALLOWED)
        self.assertIn(SUB_SCENARIO_EXECUTE, PHASE_STATUS_ALLOWED)
        self.assertIn(SUB_SCENARIO_FIX, PHASE_STATUS_ALLOWED)
        # prepare / fix: completed + failed
        self.assertEqual(
            PHASE_STATUS_ALLOWED[SUB_SCENARIO_PREPARE],
            frozenset({STATUS_COMPLETED, STATUS_FAILED}),
        )
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
# 4. reducer: scenario-prepare
# ============================================================

class TestScenarioPrepare(unittest.TestCase):
    def test_prepare_completed_dispatches_execute(self):
        state = _make_state()
        # 模拟场景：scenario-prepare 已 running
        state = state.replace(
            tracks={
                **state.tracks,
                "real-integration.scenario-test": state.tracks[
                    "real-integration.scenario-test"
                ].replace(
                    phases={
                        SUB_SCENARIO_PREPARE: PhaseState(status="running", attempt=1),
                    },
                ),
            },
        )
        record = PipelineRecord(
            track="real-integration.scenario-test",
            phase=SUB_SCENARIO_PREPARE,
            status=STATUS_COMPLETED,
            summary="all roles ready",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, SUB_SCENARIO_EXECUTE)
        self.assertEqual(
            new_state.tracks["real-integration.scenario-test"]
            .phases[SUB_SCENARIO_PREPARE].status,
            "completed",
        )
        self.assertEqual(new_state.current_phase, SUB_SCENARIO_EXECUTE)

    def test_prepare_failed_workflow_failed(self):
        state = _make_state()
        state = state.replace(
            tracks={
                **state.tracks,
                "real-integration.scenario-test": state.tracks[
                    "real-integration.scenario-test"
                ].replace(
                    phases={
                        SUB_SCENARIO_PREPARE: PhaseState(status="running", attempt=1),
                    },
                ),
            },
        )
        record = PipelineRecord(
            track="real-integration.scenario-test",
            phase=SUB_SCENARIO_PREPARE,
            status=STATUS_FAILED,
            summary="backend health_check failed",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "workflow_failed")
        self.assertIn("scenario-prepare", action.detail["reason"])


# ============================================================
# 5. reducer: scenario-execute
# ============================================================

class TestScenarioExecute(unittest.TestCase):
    def test_execute_completed_track_done(self):
        state = _make_state()
        # 模拟 prepare 已 completed
        t = state.tracks["real-integration.scenario-test"]
        t = t.replace(
            phases={
                SUB_SCENARIO_PREPARE: PhaseState(status="completed"),
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
            status=STATUS_COMPLETED,
            summary="all scenarios passed",
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "advance")
        # track 标记为 completed
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
                SUB_SCENARIO_PREPARE: PhaseState(status="completed"),
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
        # fix_cycles 应追加 1 条
        fix_cycles = new_state.tracks[
            "real-integration.scenario-test"
        ].phases[SUB_SCENARIO_EXECUTE].fix_cycles
        self.assertEqual(len(fix_cycles), 1)

    def test_execute_escalate_no_tasks_updated_is_error(self):
        state = _make_state()
        t = state.tracks["real-integration.scenario-test"]
        t = t.replace(
            phases={
                SUB_SCENARIO_PREPARE: PhaseState(status="completed"),
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
            status=STATUS_ESCALATE,
            summary="",
            tasks_updated=(),
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "error")
        self.assertIn("tasks_updated", action.detail["reason"])

    def test_execute_escalate_exhausted_workflow_failed(self):
        """max_fix_retries 耗尽 → workflow_failed（不复用 accept_gap）。"""
        # 设置 fix_cycles 已达 max_fix_retries
        state = _make_state(max_fix_retries=3)
        existing_cycles = tuple(
            {"cycle": i, "status": "completed"} for i in range(1, 4)
        )
        t = state.tracks["real-integration.scenario-test"]
        t = t.replace(
            phases={
                SUB_SCENARIO_PREPARE: PhaseState(status="completed"),
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
            status=STATUS_ESCALATE,
            summary="S-xyz failed again",
            tasks_updated=("S-xyz",),
        )
        new_state, action = reduce_state(state, record)
        self.assertEqual(action.kind, "workflow_failed")
        self.assertIn("exhausted", action.detail["reason"])

    def test_execute_failed_attempt_retry(self):
        state = _make_state()
        t = state.tracks["real-integration.scenario-test"]
        t = t.replace(
            phases={
                SUB_SCENARIO_PREPARE: PhaseState(status="completed"),
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
# 6. reducer: scenario-fix (子 pipeline 中的 scenario-fix)
# ============================================================

class TestScenarioFixHandler(unittest.TestCase):
    def _state_with_fix_subpipeline(self, fix_attempt: int = 1) -> PipelineState:
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
            phases={
                SUB_SCENARIO_PREPARE: PhaseState(status="completed"),
                SUB_SCENARIO_EXECUTE: PhaseState(
                    status="running",
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
        # 走 sub_pipeline 路径
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
        # fix_cycles 的最后一条标记为 completed
        fix_cycles = new_state.tracks[
            "real-integration.scenario-test"
        ].phases[SUB_SCENARIO_EXECUTE].fix_cycles
        self.assertEqual(fix_cycles[-1]["status"], "completed")

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


# ============================================================
# 7. detect.py: scenario track 路由
# ============================================================

class TestDetectScenarioAction(unittest.TestCase):
    def test_detect_initial_dispatches_prepare(self):
        state = _make_state()
        action = detect_mod.next_pending(state)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, SUB_SCENARIO_PREPARE)
        self.assertEqual(action.track, "real-integration.scenario-test")

    def test_detect_prepare_completed_dispatches_execute(self):
        t = TrackState(
            track_id="real-integration.scenario-test",
            bare="scenario-test",
            modules=("backend",),
            phases={
                SUB_SCENARIO_PREPARE: PhaseState(status="completed"),
            },
        )
        state = PipelineState(
            change="t",
            pipeline_order=("real-integration.scenario-test",),
            track_types={"real-integration.scenario-test": "scenario"},
            tracks={"real-integration.scenario-test": t},
        )
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
# 8. dispatch.py: PHASE_AGENTS + ALLOWED_STATUSES + extract_scenario_md
# ============================================================

class TestDispatchScenario(unittest.TestCase):
    def test_phase_agents_contains_scenario(self):
        self.assertEqual(
            dispatch_mod.PHASE_AGENTS.get(SUB_SCENARIO_PREPARE),
            "pg-build/scenario-prepare",
        )
        self.assertEqual(
            dispatch_mod.PHASE_AGENTS.get(SUB_SCENARIO_EXECUTE),
            "pg-build/scenario-execute",
        )
        self.assertEqual(
            dispatch_mod.PHASE_AGENTS.get(SUB_SCENARIO_FIX),
            "pg-build/scenario-fix",
        )

    def test_phase_allowed_statuses_contains_scenario(self):
        self.assertIn(SUB_SCENARIO_PREPARE, dispatch_mod.PHASE_ALLOWED_STATUSES)
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
# 9. sub_agent_contract: PHASE_RULES
# ============================================================

class TestSubAgentContractScenario(unittest.TestCase):
    def test_phase_rules_contain_scenario(self):
        for phase in (SUB_SCENARIO_PREPARE, SUB_SCENARIO_EXECUTE, SUB_SCENARIO_FIX):
            self.assertIn(phase, PHASE_RULES)

    def test_scenario_execute_escalate_only_tasks(self):
        rule = PHASE_RULES[SUB_SCENARIO_EXECUTE]
        self.assertEqual(rule["tasks_updated_required"], "escalate_only")


# ============================================================
# 10. orchestrator-level: 完整 prepare → execute → fix → execute → completed
# ============================================================

class TestScenarioTrackEnd2End(unittest.TestCase):
    def test_full_lifecycle_no_failure(self):
        """完整路径：prepare.completed → execute.completed → track 完成。"""
        state = _make_state()
        # 记录 prepare.completed
        state, _ = reduce_state(
            state,
            PipelineRecord(
                track="real-integration.scenario-test",
                phase=SUB_SCENARIO_PREPARE,
                status=STATUS_COMPLETED,
                summary="roles ready",
            ),
        )
        self.assertEqual(state.current_phase, SUB_SCENARIO_EXECUTE)
        # 记录 execute.completed
        new_state, action = reduce_state(
            state,
            PipelineRecord(
                track="real-integration.scenario-test",
                phase=SUB_SCENARIO_EXECUTE,
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
        """完整路径：prepare.completed → execute.escalate → fix.completed → execute.completed。"""
        state = _make_state()

        # Step 1: prepare.completed
        state, _ = reduce_state(
            state,
            PipelineRecord(
                track="real-integration.scenario-test",
                phase=SUB_SCENARIO_PREPARE,
                status=STATUS_COMPLETED,
            ),
        )

        # Step 2: execute.escalate
        state, action = reduce_state(
            state,
            PipelineRecord(
                track="real-integration.scenario-test",
                phase=SUB_SCENARIO_EXECUTE,
                status=STATUS_ESCALATE,
                tasks_updated=("S-fail",),
                summary="S-fail failed",
            ),
        )
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, SUB_SCENARIO_FIX)

        # Step 3: fix.completed → 应回到 execute
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

        # Step 4: execute.completed → track 完成
        new_state, action = reduce_state(
            state,
            PipelineRecord(
                track="real-integration.scenario-test",
                phase=SUB_SCENARIO_EXECUTE,
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

        # Step 1: prepare.completed
        state, _ = reduce_state(
            state,
            PipelineRecord(
                track="real-integration.scenario-test",
                phase=SUB_SCENARIO_PREPARE,
                status=STATUS_COMPLETED,
            ),
        )

        # Step 2-3: 第一次 escalate + fix（这是允许的 1 次 fix cycle）
        state, _ = reduce_state(
            state,
            PipelineRecord(
                track="real-integration.scenario-test",
                phase=SUB_SCENARIO_EXECUTE,
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

        # Step 4: 第二次 escalate → max_fix_retries=1 已耗尽 → workflow_failed
        new_state, action = reduce_state(
            state,
            PipelineRecord(
                track="real-integration.scenario-test",
                phase=SUB_SCENARIO_EXECUTE,
                status=STATUS_ESCALATE,
                tasks_updated=("S-2",),
            ),
        )
        self.assertEqual(action.kind, "workflow_failed")
        self.assertIn("exhausted", action.detail["reason"])


if __name__ == "__main__":
    unittest.main()
