"""v2.6 / v3.x: state.py schema 扩展单测 — review phase + 新字段。

v3.x 重构：
- 旧字段 code_review_enabled / code_review_profiles / code_review_profile / code_review_languages 已删除
- 新字段 code_review_enabled（从 execution-manifest.yaml 派生的 bool 字段）
- 旧 snapshot 兼容：从 code_review_enabled 旧值回填到 code_review_enabled

覆盖：
- SUB_PHASES 顺序含 review
- PhaseState.review_fix_cycles 字段
- TrackState.code_review_enabled 派生字段
- 序列化往返
- legacy snapshot 兼容（v2.6 旧字段 → v3.x 派生字段）
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
    REVIEW_SUB,
    FIX_REVIEW_SUB,
)


class TestSubPhases(unittest.TestCase):
    """v2.6: SUB_PHASES 顺序含 review。"""

    def test_sub_phases_includes_review(self):
        self.assertIn("review", SUB_PHASES)

    def test_sub_phases_order(self):
        # test → dev → review → verify → gate
        self.assertEqual(
            SUB_PHASES,
            ("test", "dev", "review", "verify", "gate"),
        )

    def test_review_constants(self):
        self.assertEqual(REVIEW_SUB, "review")
        self.assertEqual(FIX_REVIEW_SUB, "fix-review")


class TestPhaseStateReview(unittest.TestCase):
    """v2.6: PhaseState 新增 review_fix_cycles 字段。"""

    def test_default_empty(self):
        ps = PhaseState()
        self.assertEqual(ps.review_fix_cycles, ())

    def test_set_review_fix_cycles(self):
        ps = PhaseState(review_fix_cycles=(
            {"cycle": 1, "status": "completed"},
            {"cycle": 2, "status": "failed"},
        ))
        self.assertEqual(len(ps.review_fix_cycles), 2)
        self.assertEqual(ps.review_fix_cycles[0]["cycle"], 1)
        self.assertEqual(ps.review_fix_cycles[1]["status"], "failed")

    def test_to_dict_includes_field(self):
        ps = PhaseState(review_fix_cycles=(
            {"cycle": 1, "status": "completed"},
        ))
        d = ps.to_dict()
        self.assertIn("review_fix_cycles", d)
        self.assertEqual(len(d["review_fix_cycles"]), 1)

    def test_to_dict_omits_when_empty(self):
        ps = PhaseState()
        d = ps.to_dict()
        self.assertNotIn("review_fix_cycles", d)

    def test_from_dict_with_field(self):
        d = {
            "status": "pending",
            "review_fix_cycles": [
                {"cycle": 1, "status": "pending"},
            ],
        }
        ps = PhaseState.from_dict(d)
        self.assertEqual(len(ps.review_fix_cycles), 1)

    def test_from_dict_missing_field_defaults_to_empty(self):
        d = {"status": "pending"}
        ps = PhaseState.from_dict(d)
        self.assertEqual(ps.review_fix_cycles, ())

    def test_roundtrip(self):
        ps = PhaseState(
            status="completed",
            attempt=2,
            summary="cv pass",
            review_fix_cycles=(
                {"cycle": 1, "status": "completed"},
            ),
        )
        d = ps.to_dict()
        ps2 = PhaseState.from_dict(d)
        self.assertEqual(ps, ps2)


class TestTrackStateReviewEnabled(unittest.TestCase):
    """v2.6: TrackState review 配置字段。"""

    def test_defaults(self):
        t = TrackState.create("dev.backend")
        self.assertTrue(t.code_review_enabled)
        self.assertEqual(t.code_review_profiles, ())
        self.assertEqual(t.code_review_profile, "")
        self.assertEqual(t.code_review_languages, ())
        self.assertEqual(t.max_review_fix_retries, 3)

    def test_set_explicit(self):
        t = TrackState.create(
            "dev.backend",
            code_review_enabled=True,
            code_review_profiles=("security", "java-spring"),
            code_review_profile="",
            code_review_languages=("java",),
            max_review_fix_retries=5,
        )
        self.assertTrue(t.code_review_enabled)
        self.assertEqual(t.code_review_profiles, ("security", "java-spring"))
        self.assertEqual(t.code_review_languages, ("java",))
        self.assertEqual(t.max_review_fix_retries, 5)

    def test_simple_track_auto_disabled(self):
        """simple track 默认 code_review_enabled=False（由 orchestrator 设置）。"""
        t = TrackState.create("proto-gen", code_review_enabled=False)
        self.assertFalse(t.code_review_enabled)

    def test_to_dict_includes_all_fields(self):
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
        self.assertIn("max_review_fix_retries", d)

    def test_from_dict_legacy_defaults(self):
        """旧 snapshot 缺少新字段 → default 填充，不破坏 replay。"""
        d = {"track_id": "dev.backend", "bare": "backend", "status": "running", "phases": {}}
        t = TrackState.from_dict(d)
        self.assertTrue(t.code_review_enabled)
        self.assertEqual(t.code_review_profiles, ())
        self.assertEqual(t.max_review_fix_retries, 3)

    def test_roundtrip(self):
        t = TrackState.create(
            "dev.backend",
            modules=("backend",),
            code_review_enabled=True,
            code_review_profiles=("security",),
            code_review_languages=("java",),
            max_review_fix_retries=4,
        )
        d = t.to_dict()
        t2 = TrackState.from_dict(d)
        self.assertEqual(t, t2)
        self.assertFalse(t.code_review_enabled)

    def test_from_dict_legacy_defaults(self):
        """旧 snapshot 缺字段 → 默认 True（兼容 v2.6 默认行为）。"""
        d = {"track_id": "dev.backend", "bare": "backend", "status": "running", "phases": {}}
        t = TrackState.from_dict(d)
        self.assertTrue(t.code_review_enabled)
        self.assertEqual(t.max_review_fix_retries, 3)

    def test_roundtrip(self):
        t = TrackState.create(
            "dev.backend",
            modules=("backend",),
            code_review_enabled=True,
            code_review_profiles=("security", "java-spring"),
            code_review_languages=("java",),
            max_review_fix_retries=4,
        )
        d = t.to_dict()
        t2 = TrackState.from_dict(d)
        self.assertEqual(t, t2)


class TestPipelineStateReviewCompat(unittest.TestCase):
    """PipelineState 含 review 配置字段的兼容性。"""

    def test_legacy_snapshot_loads(self):
        """legacy snapshot（不含 review 字段）应正常加载。"""
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
        # 缺字段时默认 True（兼容 v2.6），实际由 bootstrap 从 manifest 派生覆盖
        self.assertTrue(t.code_review_enabled)


if __name__ == "__main__":
    unittest.main()
