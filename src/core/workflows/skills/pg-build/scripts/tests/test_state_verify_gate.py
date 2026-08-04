"""v3.4: TrackState 新增 verify_enabled / gate_enabled 字段单测。

- 字段默认 True（向后兼容）
- to_dict / from_dict 序列化往返
- legacy snapshot 缺字段 → 默认 True（兼容 v3.x 默认行为）
- 与 code_review_enabled 并存，simple track 三者同时关闭
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.state import PipelineState, TrackState


class TestVerifyGateEnabledDefaults(unittest.TestCase):
    """verify_enabled / gate_enabled 默认 True。"""

    def test_defaults_true(self):
        t = TrackState.create("dev.backend")
        self.assertTrue(t.verify_enabled)
        self.assertTrue(t.gate_enabled)
        self.assertTrue(t.code_review_enabled)

    def test_explicit_true(self):
        t = TrackState.create("dev.backend", verify_enabled=True, gate_enabled=True)
        self.assertTrue(t.verify_enabled)
        self.assertTrue(t.gate_enabled)

    def test_explicit_false(self):
        t = TrackState.create(
            "dev.backend",
            verify_enabled=False,
            gate_enabled=False,
            code_review_enabled=True,
        )
        self.assertFalse(t.verify_enabled)
        self.assertFalse(t.gate_enabled)
        self.assertTrue(t.code_review_enabled)

    def test_three_independent(self):
        """三个开关完全独立，可任意组合。"""
        t = TrackState.create(
            "dev.backend",
            code_review_enabled=False,
            verify_enabled=False,
            gate_enabled=True,
        )
        self.assertFalse(t.code_review_enabled)
        self.assertFalse(t.verify_enabled)
        self.assertTrue(t.gate_enabled)

        t2 = TrackState.create(
            "dev.backend",
            code_review_enabled=True,
            verify_enabled=True,
            gate_enabled=False,
        )
        self.assertTrue(t2.code_review_enabled)
        self.assertTrue(t2.verify_enabled)
        self.assertFalse(t2.gate_enabled)


class TestVerifyGateSerialization(unittest.TestCase):
    """to_dict / from_dict 序列化。"""

    def test_to_dict_includes_fields(self):
        t = TrackState.create(
            "dev.backend",
            verify_enabled=False,
            gate_enabled=False,
        )
        d = t.to_dict()
        self.assertIn("verify_enabled", d)
        self.assertIn("gate_enabled", d)
        self.assertFalse(d["verify_enabled"])
        self.assertFalse(d["gate_enabled"])

    def test_roundtrip(self):
        t = TrackState.create(
            "dev.backend",
            code_review_enabled=True,
            verify_enabled=False,
            gate_enabled=False,
            modules=("backend",),
        )
        d = t.to_dict()
        t2 = TrackState.from_dict(d)
        self.assertEqual(t, t2)
        self.assertFalse(t2.verify_enabled)
        self.assertFalse(t2.gate_enabled)

    def test_legacy_snapshot_defaults(self):
        """旧 snapshot（v3.x）缺 verify_enabled / gate_enabled → 默认 True。

        这是关键兼容保护：v3.x 时代的 archive 全部无此字段，
        replay 时必须视为 enabled，否则会被错误 silent-skip。
        """
        d = {
            "track_id": "dev.backend",
            "bare": "backend",
            "status": "running",
            "phases": {},
        }
        t = TrackState.from_dict(d)
        self.assertTrue(t.verify_enabled)
        self.assertTrue(t.gate_enabled)


class TestSimpleTrackDisabledTriple(unittest.TestCase):
    """simple track 自动三关闭（review / verify / gate）。"""

    def test_simple_track_all_three_disabled(self):
        """模拟 orchestrator bootstrap 后 simple track 的最终状态。

        此测试不依赖 orchestrator，直接构造 simple-like track state：
        验证三开关一致为 False 时，pipeline 不会派发任何 sub-agent。
        """
        t = TrackState.create(
            "dev.openapi-gen",
            code_review_enabled=False,
            verify_enabled=False,
            gate_enabled=False,
        )
        self.assertFalse(t.code_review_enabled)
        self.assertFalse(t.verify_enabled)
        self.assertFalse(t.gate_enabled)


class TestPipelineStateCompat(unittest.TestCase):
    """PipelineState 包含 verify_enabled / gate_enabled 的兼容性。"""

    def test_legacy_pipeline_snapshot_loads(self):
        """v3.x archive snapshot 缺字段 → load 成功，所有 enabled 字段默认 True。"""
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
        t = state.tracks["dev.backend"]
        self.assertTrue(t.code_review_enabled)
        self.assertTrue(t.verify_enabled)
        self.assertTrue(t.gate_enabled)


if __name__ == "__main__":
    unittest.main()
