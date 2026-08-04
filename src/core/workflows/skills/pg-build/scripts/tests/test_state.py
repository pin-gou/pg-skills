"""PipelineState 单元测试。覆盖序列化、frozen 不可变、派生方法。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.state import (
    PhaseState,
    PipelineState,
    TrackState,
)
from pipeline.sub_pipeline import SubPipeline


class TestPhaseState(unittest.TestCase):
    def test_default_values(self):
        p = PhaseState()
        self.assertEqual(p.status, "pending")
        self.assertEqual(p.attempt, 0)
        self.assertIsNone(p.started_at)

    def test_replace_returns_new_instance(self):
        p = PhaseState()
        p2 = p.replace(status="running", attempt=1)
        self.assertEqual(p.status, "pending")  # 原对象未变
        self.assertEqual(p2.status, "running")
        self.assertEqual(p2.attempt, 1)

    def test_to_dict_roundtrip(self):
        p = PhaseState(status="completed", attempt=2, summary="done")
        d = p.to_dict()
        p2 = PhaseState.from_dict(d)
        self.assertEqual(p.status, p2.status)
        self.assertEqual(p.attempt, p2.attempt)
        self.assertEqual(p.summary, p2.summary)

    def test_frozen(self):
        p = PhaseState()
        with self.assertRaises(Exception):
            p.status = "running"  # type: ignore[misc]


class TestSubPipeline(unittest.TestCase):
    def test_create(self):
        sp = SubPipeline(
            pipeline_id="dev.backend.fix-1",
            parent_track="dev.backend",
            parent_phase="verify",
            cycle=1,
            kind="fix-cycle",
            phases=("fix", "verify"),
        )
        self.assertEqual(sp.pipeline_id, "dev.backend.fix-1")
        self.assertEqual(sp.cycle, 1)
        self.assertEqual(sp.phases, ("fix", "verify"))

    def test_to_dict_roundtrip(self):
        sp = SubPipeline(
            pipeline_id="x",
            parent_track="t",
            parent_phase="verify",
            cycle=2,
            kind="fix-cycle",
            phases=("fix", "verify"),
            status="running",
            current_index=0,
        )
        d = sp.to_dict()
        sp2 = SubPipeline.from_dict(d)
        self.assertEqual(sp.pipeline_id, sp2.pipeline_id)
        self.assertEqual(sp.cycle, sp2.cycle)
        self.assertEqual(sp.current_phase, sp2.current_phase)


class TestTrackState(unittest.TestCase):
    def test_default(self):
        t = TrackState(track_id="dev.backend", bare="backend")
        self.assertEqual(t.status, "pending")
        self.assertEqual(t.phases, {})
        self.assertEqual(t.bare, "backend")

    def test_create_auto_bare(self):
        """create() 工厂方法：bare 自动从 track_id 派生。"""
        t = TrackState.create("dev.backend")
        self.assertEqual(t.bare, "backend")
        t2 = TrackState.create("simple-track")
        self.assertEqual(t2.bare, "simple-track")

    def test_get_phase_returns_default_if_missing(self):
        t = TrackState(track_id="dev.backend", bare="backend")
        p = t.get_phase("test")
        self.assertEqual(p.status, "pending")

    def test_to_dict_roundtrip_with_phases(self):
        t = TrackState(
            track_id="dev.backend",
            bare="backend",
            phases={
                "test": PhaseState(status="completed"),
                "dev": PhaseState(status="running"),
            },
        )
        d = t.to_dict()
        t2 = TrackState.from_dict(d)
        self.assertEqual(t.track_id, t2.track_id)
        self.assertEqual(t.phases["test"].status, t2.phases["test"].status)
        self.assertEqual(t.phases["dev"].status, t2.phases["dev"].status)

    def test_enriched_fields_roundtrip(self):
        """验证富化字段的序列化/反序列化。"""
        t = TrackState(
            track_id="dev.backend",
            bare="backend",
            module_roots="['webvirt-backend']",
            module_details="- module: backend\n  - root: webvirt-backend",
            test_commands="cd webvirt-backend && mvn test",
            env_name="dev-local",
            env_instances_yaml="backend:\n  - name: backend-1\n    host: localhost\n    port: 9080",
            hooks_yaml="backend:\n  start:\n    script: .pg/hooks/role-backend-start.sh",
            prepare_log_path="2-build/prepare_env.log",
            prepare_status="ok",
            tasks_by_phase={"test": "- [ ] 1.1 test task", "dev": "- [ ] 2.1 dev task"},
            label="Backend Module",
        )
        d = t.to_dict()
        t2 = TrackState.from_dict(d)
        self.assertEqual(t.module_roots, t2.module_roots)
        self.assertEqual(t.module_details, t2.module_details)
        self.assertEqual(t.test_commands, t2.test_commands)
        self.assertEqual(t.env_name, t2.env_name)
        self.assertEqual(t.env_instances_yaml, t2.env_instances_yaml)
        self.assertEqual(t.hooks_yaml, t2.hooks_yaml)
        self.assertEqual(t.prepare_log_path, t2.prepare_log_path)
        self.assertEqual(t.prepare_status, t2.prepare_status)
        self.assertEqual(t.tasks_by_phase, t2.tasks_by_phase)
        self.assertEqual(t.label, t2.label)


class TestPipelineState(unittest.TestCase):
    def test_empty_state(self):
        s = PipelineState(change="my-change")
        self.assertEqual(s.change, "my-change")
        self.assertEqual(s.pipeline_order, ())
        self.assertEqual(s.tracks, {})
        self.assertEqual(s.status, "pending")

    def test_is_track_completed(self):
        s = PipelineState(
            change="x",
            pipeline_order=("dev.backend", "dev.frontend"),
            tracks={
                "dev.backend": TrackState.create("dev.backend", status="completed"),
                "dev.frontend": TrackState.create("dev.frontend", status="running"),
            },
        )
        self.assertTrue(s.is_track_completed("dev.backend"))
        self.assertFalse(s.is_track_completed("dev.frontend"))

    def test_all_tracks_completed_empty_order(self):
        s = PipelineState(change="x")
        self.assertFalse(s.all_tracks_completed())

    def test_all_tracks_completed_partial(self):
        s = PipelineState(
            change="x",
            pipeline_order=("a", "b"),
            tracks={
                "a": TrackState.create("a", status="completed"),
                "b": TrackState.create("b", status="running"),
            },
        )
        self.assertFalse(s.all_tracks_completed())

    def test_all_tracks_completed_full(self):
        s = PipelineState(
            change="x",
            pipeline_order=("a", "b"),
            tracks={
                "a": TrackState.create("a", status="completed"),
                "b": TrackState.create("b", status="completed"),
            },
        )
        self.assertTrue(s.all_tracks_completed())

    def test_to_dict_roundtrip(self):
        s = PipelineState(
            change="my-change",
            pipeline_order=("dev.backend",),
            tracks={"dev.backend": TrackState.create("dev.backend", status="running")},
            feature_branch="feat/pg/my-change",
            init_committed=True,
            init_commit_sha="abc123",
        )
        d = s.to_dict()
        s2 = PipelineState.from_dict(d)
        self.assertEqual(s.change, s2.change)
        self.assertEqual(s.pipeline_order, s2.pipeline_order)
        self.assertEqual(s.feature_branch, s2.feature_branch)
        self.assertEqual(s.init_commit_sha, s2.init_commit_sha)
        self.assertEqual(s.tracks["dev.backend"].status, s2.tracks["dev.backend"].status)

    def test_replace_immutable(self):
        s = PipelineState(change="x")
        s2 = s.replace(status="running", init_committed=True)
        self.assertEqual(s.status, "pending")
        self.assertEqual(s2.status, "running")
        self.assertTrue(s2.init_committed)


class TestEventsCompat(unittest.TestCase):
    """验证 events.py 的常量与 v1 ALLOWED_STATUS 一致。"""

    def test_sub_constants(self):
        from pipeline.events import (
            ALL_SUBS, SUB_TEST, SUB_DEV, SUB_VERIFY, SUB_GATE,
            SUB_FIX, SUB_FIX_GATE, SUB_SIMPLE,
        )
        self.assertIn(SUB_TEST, ALL_SUBS)
        self.assertIn(SUB_DEV, ALL_SUBS)
        self.assertIn(SUB_VERIFY, ALL_SUBS)
        self.assertIn(SUB_GATE, ALL_SUBS)
        self.assertIn(SUB_FIX, ALL_SUBS)
        self.assertIn(SUB_FIX_GATE, ALL_SUBS)
        self.assertIn(SUB_SIMPLE, ALL_SUBS)

    def test_status_constants(self):
        from pipeline.events import (
            ALL_STATUSES, STATUS_COMPLETED, STATUS_FAILED,
            STATUS_ESCALATE, STATUS_PASS, STATUS_FAIL,
        )
        self.assertEqual(len(ALL_STATUSES), 5)
        self.assertEqual(
            set(ALL_STATUSES),
            {STATUS_COMPLETED, STATUS_FAILED, STATUS_ESCALATE, STATUS_PASS, STATUS_FAIL},
        )

    def test_event_type_constants(self):
        from pipeline.events import ALL_EVENT_TYPES
        # 必须包含核心 event 类型
        self.assertIn("pipeline_started", ALL_EVENT_TYPES)
        self.assertIn("dispatch_started", ALL_EVENT_TYPES)
        self.assertIn("record_received", ALL_EVENT_TYPES)
        self.assertIn("pipeline_completed", ALL_EVENT_TYPES)
        self.assertIn("workflow_failed", ALL_EVENT_TYPES)


if __name__ == "__main__":
    unittest.main()