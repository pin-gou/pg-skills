"""v3.4: detect.py next_pending 跳过被禁用 phase 单测。

- track.code_review_enabled=False → 跳过 review phase
- track.verify_enabled=False → 跳过 verify phase
- track.gate_enabled=False → 跳过 gate phase
- 多种禁用组合的 dispatch 链路
- simple track 三者全关：直接 final-gate
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.detect import next_pending
from pipeline.events import FINAL_GATE_TRACK, PipelineAction
from pipeline.state import PipelineState, PhaseState, TrackState


def _make_state(
    tracks_cfg: dict[str, dict],
    track_types: dict[str, str] | None = None,
    current_track: str = "",
    current_phase: str = "",
):
    """构造可测的 PipelineState。

    tracks_cfg: {track_id: {"phases": {phase: status, ...}, **{enable flags}}}
    current_track/current_phase: 模拟"上一个 phase 已完成"的锚点，让 detect
        从这里开始往后找下一 phase。
    """
    tracks = {}
    pipeline_order = tuple(tracks_cfg.keys())
    for tid, cfg in tracks_cfg.items():
        phases = {
            pname: PhaseState(status=cfg.get("phases", {}).get(pname, "pending"))
            for pname in ("test", "dev", "review", "verify", "gate")
        }
        tracks[tid] = TrackState.create(
            tid,
            status=cfg.get("status", "pending"),
            phases=phases,
            code_review_enabled=cfg.get("code_review_enabled", True),
            verify_enabled=cfg.get("verify_enabled", True),
            gate_enabled=cfg.get("gate_enabled", True),
        )
    return PipelineState(
        change="x",
        pipeline_order=pipeline_order,
        track_types=track_types or {},
        tracks=tracks,
        status="running",
        current_track=current_track,
        current_phase=current_phase,
    )


class TestNextPendingBasic(unittest.TestCase):
    """基础 dispatch 行为 + 禁用 review 的兼容路径。"""

    def test_full_pipeline_first_phase(self):
        """未禁用任何 phase：fresh track 从 test 开始。"""
        state = _make_state({"dev.backend": {}})
        action = next_pending(state)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.track, "dev.backend")
        self.assertEqual(action.phase, "test")

    def test_code_review_disabled_skip_review(self):
        """禁用 review：test → dev → verify → gate（跳过 review）。"""
        state = _make_state(
            {"dev.backend": {
                "phases": {"test": "completed", "dev": "completed"},
                "code_review_enabled": False,
            }},
            current_track="dev.backend",
            current_phase="dev",
        )
        action = next_pending(state)
        self.assertEqual(action.phase, "verify")


class TestVerifyGateDisable(unittest.TestCase):
    """v3.4: 关闭 verify / gate 时的 dispatch 行为。"""

    def test_verify_disabled_after_dev(self):
        """verify_enabled=false → dev+review completed 后直接 gate。"""
        state = _make_state(
            {"dev.backend": {
                "phases": {
                    "test": "completed",
                    "dev": "completed",
                    "review": "completed",
                },
                "verify_enabled": False,
            }},
            current_track="dev.backend",
            current_phase="review",
        )
        action = next_pending(state)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.track, "dev.backend")
        self.assertEqual(action.phase, "gate")

    def test_gate_disabled_after_verify_completed(self):
        """gate_enabled=false → verify completed 后整个 track 完成 → final-gate。"""
        state = _make_state(
            {"dev.backend": {
                "phases": {
                    "test": "completed",
                    "dev": "completed",
                    "review": "completed",
                    "verify": "completed",
                },
                "gate_enabled": False,
            }},
            current_track="dev.backend",
            current_phase="verify",
        )
        action = next_pending(state)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.track, FINAL_GATE_TRACK)
        self.assertEqual(action.phase, "gate")

    def test_all_three_disabled(self):
        """三关闭 → dev completed 直接跳到 final-gate。"""
        state = _make_state(
            {"dev.backend": {
                "phases": {"test": "completed", "dev": "completed"},
                "code_review_enabled": False,
                "verify_enabled": False,
                "gate_enabled": False,
            }},
            current_track="dev.backend",
            current_phase="dev",
        )
        action = next_pending(state)
        self.assertEqual(action.kind, "dispatch")
        self.assertEqual(action.track, FINAL_GATE_TRACK)

    def test_only_review_disabled_full_path(self):
        """仅 review 关闭 → verify 出现（不带 review）。"""
        state = _make_state(
            {"dev.backend": {
                "phases": {"test": "completed", "dev": "completed"},
                "code_review_enabled": False,
                "verify_enabled": True,
                "gate_enabled": True,
            }},
            current_track="dev.backend",
            current_phase="dev",
        )
        action = next_pending(state)
        self.assertEqual(action.phase, "verify")

    def test_verify_and_gate_disabled(self):
        """verify + gate 都关闭 → dev+review completed 后直接 final-gate。"""
        state = _make_state(
            {"dev.backend": {
                "phases": {
                    "test": "completed",
                    "dev": "completed",
                    "review": "completed",
                },
                "verify_enabled": False,
                "gate_enabled": False,
            }},
            current_track="dev.backend",
            current_phase="review",
        )
        action = next_pending(state)
        self.assertEqual(action.track, FINAL_GATE_TRACK)


class TestSimpleTrackAlwaysSimple(unittest.TestCase):
    """simple track 不受 verify_enabled 影响（simple 走 simple phase）。"""

    def test_simple_track_with_all_disabled(self):
        """simple track 即使三关闭，依然走 simple phase，不走 final-gate。"""
        state = _make_state(
            {"dev.openapi-gen": {
                "phases": {},
                "code_review_enabled": False,
                "verify_enabled": False,
                "gate_enabled": False,
            }},
            track_types={"dev.openapi-gen": "simple"},
        )
        action = next_pending(state)
        self.assertEqual(action.phase, "simple")
        self.assertEqual(action.track, "dev.openapi-gen")


if __name__ == "__main__":
    unittest.main()
