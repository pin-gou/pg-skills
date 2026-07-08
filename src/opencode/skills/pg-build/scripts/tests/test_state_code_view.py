"""v2.6 / v3.x: state.py schema 扩展单测 — code-view phase + 新字段。

v3.x 重构：
- 旧字段 code_review_enabled / code_review_profiles / code_review_profile / code_review_languages 已删除
- 新字段 code_view_enabled（从 execution-manifest.yaml 派生的 bool 字段）
- 旧 snapshot 兼容：从 code_review_enabled 旧值回填到 code_view_enabled

覆盖：
- SUB_PHASES 顺序含 code-view
- PhaseState.code_view_fix_cycles 字段
- TrackState.code_view_enabled 派生字段
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


class TestTrackStateCodeViewEnabled(unittest.TestCase):
    """v3.x: TrackState 新增 code_view_enabled 派生字段。"""

    def test_default_disabled(self):
        """v3.x: 默认 False（manifest 未启用 code-view）。"""
        t = TrackState.create("dev.backend")
        self.assertFalse(t.code_view_enabled)
        self.assertEqual(t.max_code_view_fix_retries, 3)

    def test_set_explicit(self):
        t = TrackState.create(
            "dev.backend",
            code_view_enabled=True,
            max_code_view_fix_retries=5,
        )
        self.assertTrue(t.code_view_enabled)
        self.assertEqual(t.max_code_view_fix_retries, 5)

    def test_old_fields_removed(self):
        """v3.x: 旧字段 code_review_* 已删除。"""
        t = TrackState.create("dev.backend")
        for old_field in (
            "code_review_enabled",
            "code_review_profiles",
            "code_review_profile",
            "code_review_languages",
        ):
            self.assertFalse(
                hasattr(t, old_field),
                f"v3.x: {old_field} should be removed",
            )

    def test_to_dict_includes_code_view_enabled(self):
        t = TrackState.create("dev.backend", code_view_enabled=True)
        d = t.to_dict()
        self.assertIn("code_view_enabled", d)
        self.assertTrue(d["code_view_enabled"])

    def test_to_dict_omits_old_fields(self):
        """v3.x: to_dict 不再写旧字段。"""
        t = TrackState.create("dev.backend", code_view_enabled=True)
        d = t.to_dict()
        for old_field in (
            "code_review_enabled",
            "code_review_profiles",
            "code_review_profile",
            "code_review_languages",
        ):
            self.assertNotIn(
                old_field, d,
                f"v3.x: to_dict should not emit {old_field}",
            )

    def test_from_dict_legacy_compat_code_review_enabled_true(self):
        """v2.6 旧 snapshot 含 code_review_enabled=True → 派生 code_view_enabled=True。"""
        d = {
            "track_id": "dev.backend",
            "bare": "backend",
            "status": "running",
            "phases": {},
            "code_review_enabled": True,
        }
        t = TrackState.from_dict(d)
        self.assertTrue(t.code_view_enabled)

    def test_from_dict_legacy_compat_code_review_enabled_false(self):
        """v2.6 旧 snapshot 含 code_review_enabled=False → 派生 code_view_enabled=False。"""
        d = {
            "track_id": "dev.backend",
            "bare": "backend",
            "status": "running",
            "phases": {},
            "code_review_enabled": False,
        }
        t = TrackState.from_dict(d)
        self.assertFalse(t.code_view_enabled)

    def test_from_dict_new_field_wins(self):
        """v3.x 新字段 code_view_enabled 优先于旧字段 code_review_enabled。"""
        d = {
            "track_id": "dev.backend",
            "bare": "backend",
            "status": "running",
            "phases": {},
            "code_view_enabled": True,
            "code_review_enabled": False,  # 旧字段（应被忽略）
        }
        t = TrackState.from_dict(d)
        self.assertTrue(t.code_view_enabled)

    def test_from_dict_legacy_defaults(self):
        """v2.6 之前的 snapshot 缺字段 → 默认 True（兼容 v2.6 默认行为）。

        实际生产中，bootstrap 会从 execution-manifest.yaml 重新派生覆盖该值。
        本测试仅验证 from_dict 不会因缺字段 crash。
        """
        d = {
            "track_id": "dev.backend",
            "bare": "backend",
            "status": "running",
            "phases": {},
        }
        t = TrackState.from_dict(d)
        # 缺字段时默认 True（兼容 v2.6 行为），实际值由 manifest 派生覆盖
        self.assertTrue(t.code_view_enabled)
        self.assertEqual(t.max_code_view_fix_retries, 3)

    def test_roundtrip(self):
        t = TrackState.create(
            "dev.backend",
            modules=("backend",),
            code_view_enabled=True,
            max_code_view_fix_retries=4,
        )
        d = t.to_dict()
        t2 = TrackState.from_dict(d)
        self.assertEqual(t, t2)


class TestPipelineStateCodeViewCompat(unittest.TestCase):
    """PipelineState 含 code-view 配置字段的兼容性。"""

    def test_legacy_snapshot_loads(self):
        """legacy snapshot（不含 code_view_enabled）应正常加载。"""
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
        self.assertTrue(t.code_view_enabled)


if __name__ == "__main__":
    unittest.main()
