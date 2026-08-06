"""Tests for scenario restart cycle tracking (v3.12).

v3.12: stage_restarted 集合已移除, 改为 per-track TrackState.scenario_last_restart_attempt.
detect 在 dispatch scenario-execute 前用 (execute_phase.attempt > scenario_last_restart_attempt)
判定是否需要 restart_all_instances.

测试范围:
1. detect: 首次进入 scenario stage → restart (attempt=0 > last=-1)
2. detect: restart 完成后 → dispatch execute (attempt=0 == last=0)
3. detect: fix 后 attempt 递增 → restart (attempt=1 > last=0)
4. detect: 第二次 restart 后 → dispatch execute (attempt=1 == last=1)
5. bootstrap: cli_env_action_result restart 写 scenario_last_restart_attempt
6. bootstrap: cli_env_action_result clean_env 重置 scenario_last_restart_attempt=-1
7. state: to_dict/from_dict round-trip 保留 scenario_last_restart_attempt
8. state: from_dict 含旧 stage_restarted 字段时忽略 (向后兼容)
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from pipeline.state import (
    PipelineState,
    TrackState,
    PhaseState,
    SUB_SCENARIO_EXECUTE,
)
from pipeline import detect as detect_mod


def _make_scenario_state(
    track_id: str = "integration.scenario-test",
    stage: str = "integration",
    env: str = "dev-3tier",
    execute_attempt: int = 0,
    execute_status: str = "pending",
    last_restart: int = -1,
    stage_prepared: set | None = None,
) -> PipelineState:
    """构造一个 scenario track 的 PipelineState, 用于 detect 测试。

    Args:
        execute_attempt: scenario-execute phase 的 attempt 值
        execute_status: scenario-execute phase 的 status
        last_restart: scenario_last_restart_attempt 字段值
        stage_prepared: 已 prepare 的 stage 集合 (默认 {stage})
    """
    execute_phase = PhaseState(
        status=execute_status,
        attempt=execute_attempt,
    )
    t = TrackState(
        track_id=track_id,
        bare=track_id.rsplit(".", 1)[-1],
        label=track_id,
        modules=("backend",),
        phases={SUB_SCENARIO_EXECUTE: execute_phase},
        scenario_last_restart_attempt=last_restart,
    )
    return PipelineState(
        change="test-change",
        pipeline_order=(track_id,),
        track_types={track_id: "scenario"},
        tracks={track_id: t},
        status="running",
        current_track=track_id,
        current_phase="",
        stage_order=(stage,),
        stage_env_map={stage: env},
        stage_env_timeout={env: 600},
        current_stage=stage,
        stage_prepared=stage_prepared if stage_prepared is not None else {stage},
    )


# ============================================================
# 1-4. detect: restart 触发条件
# ============================================================

class TestDetectScenarioRestartCycle(unittest.TestCase):
    """验证 detect 在 scenario track 各阶段的 restart 决策。"""

    def test_first_entry_triggers_restart(self):
        """首次进入: attempt=0, last=-1 → 0 > -1 → env_switch restart."""
        state = _make_scenario_state(execute_attempt=0, last_restart=-1)
        action = detect_mod.next_pending(state)
        self.assertEqual(action.kind, "env_switch")
        self.assertEqual(action.phase, "restart")
        self.assertEqual(action.track, "integration.scenario-test")
        self.assertEqual(action.detail["stage"], "integration")
        self.assertEqual(action.detail["env_name"], "dev-3tier")

    def test_after_restart_dispatches_execute(self):
        """restart 完成后: attempt=0, last=0 → 0 > 0 为 False → dispatch execute."""
        state = _make_scenario_state(execute_attempt=0, last_restart=0)
        action = detect_mod.next_pending(state)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, SUB_SCENARIO_EXECUTE)
        self.assertEqual(action.track, "integration.scenario-test")

    def test_after_fix_triggers_restart_again(self):
        """fix 后 attempt 递增: attempt=1, last=0 → 1 > 0 → env_switch restart."""
        state = _make_scenario_state(execute_attempt=1, last_restart=0)
        action = detect_mod.next_pending(state)
        self.assertEqual(action.kind, "env_switch")
        self.assertEqual(action.phase, "restart")
        self.assertEqual(action.detail["stage"], "integration")

    def test_after_second_restart_dispatches_execute(self):
        """第二次 restart 后: attempt=1, last=1 → 1 > 1 为 False → dispatch execute."""
        state = _make_scenario_state(execute_attempt=1, last_restart=1)
        action = detect_mod.next_pending(state)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, SUB_SCENARIO_EXECUTE)

    def test_no_execute_phase_triggers_restart(self):
        """execute phase 不存在时 (首次进入前): attempt 视为 0, last=-1 → restart."""
        t = TrackState(
            track_id="integration.scenario-test",
            bare="scenario-test",
            phases={},
            scenario_last_restart_attempt=-1,
        )
        state = PipelineState(
            change="test-change",
            pipeline_order=("integration.scenario-test",),
            track_types={"integration.scenario-test": "scenario"},
            tracks={"integration.scenario-test": t},
            status="running",
            current_track="integration.scenario-test",
            stage_order=("integration",),
            stage_env_map={"integration": "dev-3tier"},
            current_stage="integration",
            stage_prepared={"integration"},
        )
        action = detect_mod.next_pending(state)
        self.assertEqual(action.kind, "env_switch")
        self.assertEqual(action.phase, "restart")

    def test_stage_not_prepared_triggers_prepare_env_not_restart(self):
        """stage 未 prepare 时: 应先 prepare_env, 不触发 restart."""
        state = _make_scenario_state(
            execute_attempt=0,
            last_restart=-1,
            stage_prepared=set(),
        )
        action = detect_mod.next_pending(state)
        self.assertEqual(action.kind, "env_switch")
        self.assertEqual(action.phase, "prepare_env")

    def test_no_stage_order_dispatches_directly(self):
        """无 stage_order (向后兼容): 直接 dispatch, 不检查 restart."""
        t = TrackState(
            track_id="real-integration.scenario-test",
            bare="scenario-test",
            phases={SUB_SCENARIO_EXECUTE: PhaseState(status="pending", attempt=0)},
            scenario_last_restart_attempt=-1,
        )
        state = PipelineState(
            change="test-change",
            pipeline_order=("real-integration.scenario-test",),
            track_types={"real-integration.scenario-test": "scenario"},
            tracks={"real-integration.scenario-test": t},
            status="running",
            current_track="real-integration.scenario-test",
        )
        action = detect_mod.next_pending(state)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.phase, SUB_SCENARIO_EXECUTE)


# ============================================================
# 5-6. bootstrap: cli_env_action_result restart / clean_env
# ============================================================

class TestBootstrapEnvActionResult(unittest.TestCase):
    """验证 cli_env_action_result 对 scenario_last_restart_attempt 的更新。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old_root = os.environ.get("PG_PROJECT_ROOT")
        os.environ["PG_PROJECT_ROOT"] = self.tmp
        os.makedirs(os.path.join(self.tmp, ".pg"), exist_ok=True)
        with open(os.path.join(self.tmp, ".pg", "project.yaml"), "w") as f:
            f.write("environments: {}\n")

        import bootstrap
        bootstrap.PROJECT_ROOT = self.tmp
        bootstrap.CHANGES_DIR = os.path.join(self.tmp, ".pg", "changes")
        self.change_root = bootstrap.CHANGES_DIR + "/test-change"
        os.makedirs(os.path.join(self.change_root, "2-build"), exist_ok=True)
        self.bootstrap = bootstrap

    def tearDown(self):
        if self.old_root:
            os.environ["PG_PROJECT_ROOT"] = self.old_root
        else:
            os.environ.pop("PG_PROJECT_ROOT", None)

    def _create_fake_log(self, phase_name: str) -> str:
        build_dir = os.path.join(self.change_root, "2-build")
        log_path = os.path.join(build_dir, f"{phase_name}-fake.log")
        with open(log_path, "w") as f:
            f.write("fake log\n")
        return log_path

    def test_restart_writes_scenario_last_restart_attempt(self):
        """restart 成功: 写入 scenario_last_restart_attempt = execute_phase.attempt."""
        from pipeline.snapshot import save_snapshot, load_snapshot

        execute_phase = PhaseState(status="pending", attempt=1)
        t = TrackState(
            track_id="integration.scenario-test",
            bare="scenario-test",
            phases={SUB_SCENARIO_EXECUTE: execute_phase},
            scenario_last_restart_attempt=-1,
        )
        state = PipelineState(
            change="test-change",
            pipeline_order=("integration.scenario-test",),
            track_types={"integration.scenario-test": "scenario"},
            tracks={"integration.scenario-test": t},
            current_stage="integration",
            stage_prepared={"integration"},
        )
        save_snapshot(self.change_root, state)
        log_path = self._create_fake_log("restart")

        result = self.bootstrap.cli_env_action_result(
            "test-change", "restart", "integration", "dev-3tier",
            success=True, log_path=log_path, exit_code=0,
        )
        self.assertTrue(result["ok"], f"expected ok, got: {result}")

        loaded = load_snapshot(self.change_root)
        track = loaded.tracks["integration.scenario-test"]
        self.assertEqual(track.scenario_last_restart_attempt, 1)

    def test_restart_first_entry_writes_zero(self):
        """首次 restart (attempt=0): 写入 scenario_last_restart_attempt=0."""
        from pipeline.snapshot import save_snapshot, load_snapshot

        execute_phase = PhaseState(status="pending", attempt=0)
        t = TrackState(
            track_id="integration.scenario-test",
            bare="scenario-test",
            phases={SUB_SCENARIO_EXECUTE: execute_phase},
            scenario_last_restart_attempt=-1,
        )
        state = PipelineState(
            change="test-change",
            pipeline_order=("integration.scenario-test",),
            track_types={"integration.scenario-test": "scenario"},
            tracks={"integration.scenario-test": t},
            current_stage="integration",
            stage_prepared={"integration"},
        )
        save_snapshot(self.change_root, state)
        log_path = self._create_fake_log("restart")

        result = self.bootstrap.cli_env_action_result(
            "test-change", "restart", "integration", "dev-3tier",
            success=True, log_path=log_path, exit_code=0,
        )
        self.assertTrue(result["ok"])

        loaded = load_snapshot(self.change_root)
        self.assertEqual(
            loaded.tracks["integration.scenario-test"].scenario_last_restart_attempt, 0
        )

    def test_restart_does_not_touch_stage_prepared(self):
        """restart 不应修改 stage_prepared."""
        from pipeline.snapshot import save_snapshot, load_snapshot

        t = TrackState(
            track_id="integration.scenario-test",
            bare="scenario-test",
            phases={SUB_SCENARIO_EXECUTE: PhaseState(attempt=0)},
            scenario_last_restart_attempt=-1,
        )
        state = PipelineState(
            change="test-change",
            pipeline_order=("integration.scenario-test",),
            track_types={"integration.scenario-test": "scenario"},
            tracks={"integration.scenario-test": t},
            current_stage="integration",
            stage_prepared={"integration"},
        )
        save_snapshot(self.change_root, state)
        log_path = self._create_fake_log("restart")

        self.bootstrap.cli_env_action_result(
            "test-change", "restart", "integration", "dev-3tier",
            success=True, log_path=log_path, exit_code=0,
        )
        loaded = load_snapshot(self.change_root)
        self.assertIn("integration", loaded.stage_prepared)

    def test_clean_env_resets_scenario_last_restart_attempt(self):
        """clean_env: 重置 scenario_last_restart_attempt 为 -1."""
        from pipeline.snapshot import save_snapshot, load_snapshot

        t = TrackState(
            track_id="integration.scenario-test",
            bare="scenario-test",
            phases={SUB_SCENARIO_EXECUTE: PhaseState(attempt=2)},
            scenario_last_restart_attempt=2,
        )
        state = PipelineState(
            change="test-change",
            pipeline_order=("integration.scenario-test",),
            track_types={"integration.scenario-test": "scenario"},
            tracks={"integration.scenario-test": t},
            current_stage="integration",
            stage_prepared={"integration"},
        )
        save_snapshot(self.change_root, state)
        log_path = self._create_fake_log("clean_env")

        result = self.bootstrap.cli_env_action_result(
            "test-change", "clean_env", "integration", "dev-3tier",
            success=True, log_path=log_path, exit_code=0,
        )
        self.assertTrue(result["ok"])

        loaded = load_snapshot(self.change_root)
        track = loaded.tracks["integration.scenario-test"]
        self.assertEqual(track.scenario_last_restart_attempt, -1)
        self.assertNotIn("integration", loaded.stage_prepared)

    def test_clean_env_resets_multiple_scenario_tracks_in_stage(self):
        """clean_env: 同一 stage 下多个 scenario track 全部重置."""
        from pipeline.snapshot import save_snapshot, load_snapshot

        t1 = TrackState(
            track_id="integration.scr-a",
            bare="scr-a",
            phases={SUB_SCENARIO_EXECUTE: PhaseState(attempt=1)},
            scenario_last_restart_attempt=1,
        )
        t2 = TrackState(
            track_id="integration.scr-b",
            bare="scr-b",
            phases={SUB_SCENARIO_EXECUTE: PhaseState(attempt=3)},
            scenario_last_restart_attempt=3,
        )
        # 非 scenario track 不应被重置
        t3 = TrackState(
            track_id="integration.backend",
            bare="backend",
            phases={},
        )
        state = PipelineState(
            change="test-change",
            pipeline_order=("integration.scr-a", "integration.scr-b", "integration.backend"),
            track_types={
                "integration.scr-a": "scenario",
                "integration.scr-b": "scenario",
                "integration.backend": "standard",
            },
            tracks={
                "integration.scr-a": t1,
                "integration.scr-b": t2,
                "integration.backend": t3,
            },
            current_stage="integration",
            stage_prepared={"integration"},
        )
        save_snapshot(self.change_root, state)
        log_path = self._create_fake_log("clean_env")

        self.bootstrap.cli_env_action_result(
            "test-change", "clean_env", "integration", "dev-3tier",
            success=True, log_path=log_path, exit_code=0,
        )
        loaded = load_snapshot(self.change_root)
        self.assertEqual(loaded.tracks["integration.scr-a"].scenario_last_restart_attempt, -1)
        self.assertEqual(loaded.tracks["integration.scr-b"].scenario_last_restart_attempt, -1)

    def test_restart_only_affects_matching_stage(self):
        """restart 只更新 stage 匹配的 scenario track, 不影响其他 stage."""
        from pipeline.snapshot import save_snapshot, load_snapshot

        t_int = TrackState(
            track_id="integration.scenario-test",
            bare="scenario-test",
            phases={SUB_SCENARIO_EXECUTE: PhaseState(attempt=1)},
            scenario_last_restart_attempt=-1,
        )
        t_dev = TrackState(
            track_id="dev.scenario-other",
            bare="scenario-other",
            phases={SUB_SCENARIO_EXECUTE: PhaseState(attempt=5)},
            scenario_last_restart_attempt=5,
        )
        state = PipelineState(
            change="test-change",
            pipeline_order=("integration.scenario-test", "dev.scenario-other"),
            track_types={
                "integration.scenario-test": "scenario",
                "dev.scenario-other": "scenario",
            },
            tracks={
                "integration.scenario-test": t_int,
                "dev.scenario-other": t_dev,
            },
            current_stage="integration",
            stage_prepared={"integration", "dev"},
        )
        save_snapshot(self.change_root, state)
        log_path = self._create_fake_log("restart")

        self.bootstrap.cli_env_action_result(
            "test-change", "restart", "integration", "dev-3tier",
            success=True, log_path=log_path, exit_code=0,
        )
        loaded = load_snapshot(self.change_root)
        self.assertEqual(
            loaded.tracks["integration.scenario-test"].scenario_last_restart_attempt, 1
        )
        # dev stage 的 track 不受影响
        self.assertEqual(
            loaded.tracks["dev.scenario-other"].scenario_last_restart_attempt, 5
        )


# ============================================================
# 7-8. state: 序列化兼容性
# ============================================================

class TestStateSerialization(unittest.TestCase):
    """验证 scenario_last_restart_attempt 的序列化与向后兼容。"""

    def test_to_dict_includes_scenario_last_restart_attempt(self):
        t = TrackState(
            track_id="integration.scenario-test",
            bare="scenario-test",
            scenario_last_restart_attempt=3,
        )
        d = t.to_dict()
        self.assertEqual(d["scenario_last_restart_attempt"], 3)

    def test_from_dict_reads_scenario_last_restart_attempt(self):
        d = {
            "track_id": "integration.scenario-test",
            "bare": "scenario-test",
            "scenario_last_restart_attempt": 2,
        }
        t = TrackState.from_dict(d)
        self.assertEqual(t.scenario_last_restart_attempt, 2)

    def test_from_dict_defaults_to_minus_one(self):
        d = {"track_id": "integration.scenario-test", "bare": "scenario-test"}
        t = TrackState.from_dict(d)
        self.assertEqual(t.scenario_last_restart_attempt, -1)

    def test_round_trip_preserves_value(self):
        t = TrackState(
            track_id="integration.scenario-test",
            bare="scenario-test",
            scenario_last_restart_attempt=7,
        )
        d = t.to_dict()
        t2 = TrackState.from_dict(d)
        self.assertEqual(t2.scenario_last_restart_attempt, 7)

    def test_pipeline_state_ignores_legacy_stage_restarted(self):
        """旧 snapshot 含 stage_restarted 字段时, from_dict 应忽略 (不报错)."""
        d = {
            "change": "test-change",
            "pipeline_order": ["integration.scenario-test"],
            "track_types": {"integration.scenario-test": "scenario"},
            "tracks": {
                "integration.scenario-test": {
                    "track_id": "integration.scenario-test",
                    "bare": "scenario-test",
                }
            },
            "stage_restarted": ["integration"],  # legacy field
        }
        state = PipelineState.from_dict(d)
        # stage_restarted 应被忽略, 不应出现在 state 中
        self.assertFalse(hasattr(state, "stage_restarted"))
        self.assertEqual(state.change, "test-change")

    def test_pipeline_state_to_dict_has_no_stage_restarted(self):
        """to_dict 不应输出 stage_restarted 字段."""
        t = TrackState(track_id="t", bare="t")
        state = PipelineState(
            change="test-change",
            pipeline_order=("t",),
            tracks={"t": t},
        )
        d = state.to_dict()
        self.assertNotIn("stage_restarted", d)


if __name__ == "__main__":
    unittest.main()
