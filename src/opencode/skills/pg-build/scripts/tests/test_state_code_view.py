"""v2.6: state.py schema 扩展单测 — code-view phase + 新字段。

覆盖：
- SUB_PHASES 顺序含 code-view
- PhaseState.code_view_fix_cycles 字段
- TrackState.code_review_* 字段（含 simple track 自动 false）
- 序列化往返
- legacy snapshot 兼容（缺失字段 default）
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.state import (
    PipelineState,
    TrackState,
    PhaseState,
    SUB_PHASES,
    CODE_VIEW_SUB,
    FIX_CODE_VIEW_SUB,
)


class TestSubPhases(unittest.TestCase):
    """v2.6: SUB_PHASES 顺序含 code-view。"""

    def test_sub_phases_includes_code_view(self):
        self.assertIn("code-view", SUB_PHASES)

    def test_sub_phases_order(self):
        # test → dev → code-view → verify → gate
        self.assertEqual(
            SUB_PHASES,
            ("test", "dev", "code-view", "verify", "gate"),
        )

    def test_code_view_constants(self):
        self.assertEqual(CODE_VIEW_SUB, "code-view")
        self.assertEqual(FIX_CODE_VIEW_SUB, "fix-code-view")


class TestPhaseStateCodeView(unittest.TestCase):
    """v2.6: PhaseState 新增 code_view_fix_cycles 字段。"""

    def test_default_empty(self):
        ps = PhaseState()
        self.assertEqual(ps.code_view_fix_cycles, ())

    def test_set_code_view_fix_cycles(self):
        ps = PhaseState(code_view_fix_cycles=(
            {"cycle": 1, "status": "completed"},
            {"cycle": 2, "status": "failed"},
        ))
        self.assertEqual(len(ps.code_view_fix_cycles), 2)
        self.assertEqual(ps.code_view_fix_cycles[0]["cycle"], 1)
        self.assertEqual(ps.code_view_fix_cycles[1]["status"], "failed")

    def test_to_dict_includes_field(self):
        ps = PhaseState(code_view_fix_cycles=(
            {"cycle": 1, "status": "completed"},
        ))
        d = ps.to_dict()
        self.assertIn("code_view_fix_cycles", d)
        self.assertEqual(len(d["code_view_fix_cycles"]), 1)

    def test_to_dict_omits_when_empty(self):
        ps = PhaseState()
        d = ps.to_dict()
        self.assertNotIn("code_view_fix_cycles", d)

    def test_from_dict_with_field(self):
        d = {
            "status": "pending",
            "code_view_fix_cycles": [
                {"cycle": 1, "status": "pending"},
            ],
        }
        ps = PhaseState.from_dict(d)
        self.assertEqual(len(ps.code_view_fix_cycles), 1)

    def test_from_dict_missing_field_defaults_to_empty(self):
        d = {"status": "pending"}
        ps = PhaseState.from_dict(d)
        self.assertEqual(ps.code_view_fix_cycles, ())

    def test_roundtrip(self):
        ps = PhaseState(
            status="completed",
            attempt=2,
            summary="cv pass",
            code_view_fix_cycles=(
                {"cycle": 1, "status": "completed"},
            ),
        )
        d = ps.to_dict()
        ps2 = PhaseState.from_dict(d)
        self.assertEqual(ps, ps2)


class TestTrackStateCodeReview(unittest.TestCase):
    """v2.6: TrackState 新增 code_review 配置字段。"""

    def test_defaults(self):
        t = TrackState.create("dev.backend")
        self.assertTrue(t.code_review_enabled)
        self.assertEqual(t.code_review_profiles, ())
        self.assertEqual(t.code_review_profile, "")
        self.assertEqual(t.code_review_languages, ())
        self.assertEqual(t.max_code_view_fix_retries, 3)

    def test_set_explicit(self):
        t = TrackState.create(
            "dev.backend",
            code_review_enabled=False,
            code_review_profiles=("security", "java-spring"),
            code_review_profile="",
            code_review_languages=("java",),
            max_code_view_fix_retries=5,
        )
        self.assertFalse(t.code_review_enabled)
        self.assertEqual(t.code_review_profiles, ("security", "java-spring"))
        self.assertEqual(t.code_review_languages, ("java",))
        self.assertEqual(t.max_code_view_fix_retries, 5)

    def test_simple_track_auto_disabled(self):
        """simple track 默认 code_review_enabled=False（由 orchestrator 设置）。"""
        # 模拟 orchestrator._first_next 的行为：simple 强制 false
        t = TrackState.create(
            "proto-gen",
            code_review_enabled=False,  # orchestrator 自动设置
        )
        self.assertFalse(t.code_review_enabled)

    def test_to_dict_includes_code_review(self):
        t = TrackState.create(
            "dev.backend",
            code_review_profiles=("java-spring",),
            code_review_languages=("java",),
        )
        d = t.to_dict()
        self.assertIn("code_review_enabled", d)
        self.assertIn("code_review_profiles", d)
        self.assertIn("code_review_profile", d)
        self.assertIn("code_review_languages", d)
        self.assertIn("max_code_view_fix_retries", d)

    def test_from_dict_legacy_defaults(self):
        """v2.6 之前的 snapshot 缺少新字段 → default 填充，不破坏 replay。"""
        d = {
            "track_id": "dev.backend",
            "bare": "backend",
            "status": "running",
            "phases": {},
        }
        t = TrackState.from_dict(d)
        # 默认值生效
        self.assertTrue(t.code_review_enabled)
        self.assertEqual(t.code_review_profiles, ())
        self.assertEqual(t.max_code_view_fix_retries, 3)

    def test_roundtrip(self):
        t = TrackState.create(
            "dev.backend",
            modules=("backend",),
            code_review_enabled=True,
            code_review_profiles=("security",),
            code_review_languages=("java",),
            max_code_view_fix_retries=4,
        )
        d = t.to_dict()
        t2 = TrackState.from_dict(d)
        self.assertEqual(t, t2)


class TestPipelineStateCodeViewCompat(unittest.TestCase):
    """PipelineState 含 code-view 配置字段的兼容性。"""

    def test_legacy_snapshot_loads(self):
        """legacy snapshot（不含 code_view_fix_cycles）应正常加载。"""
        d = {
            "schema_version": "2026-06-30",
            "change": "x",
            "pipeline_order": ("dev.backend",),
            "tracks": {
                "dev.backend": {
                    "track_id": "dev.backend",
                    "bare": "backend",
                    "status": "pending",
                    "phases": {},
                },
            },
        }
        state = PipelineState.from_dict(d)
        self.assertEqual(state.change, "x")
        t = state.tracks["dev.backend"]
        self.assertTrue(t.code_review_enabled)
        self.assertEqual(t.code_review_profiles, ())


if __name__ == "__main__":
    unittest.main()