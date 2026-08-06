#!/usr/bin/env python3
"""v1.3: migrate-define-summary.py 单测.

覆盖:
- 旧 V-NNN → 新 V-{track}-N 改写
- 已有 track_id 不被 --track 覆盖
- 未指定 --track + 已有 track_id → 推断
- 未指定 --track 且无 track_id → exit 2
- dry-run 不写盘
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

try:
    import yaml
except ImportError:
    yaml = None

_SCRIPT = "/home/ubuntu/workspace/oc1-web-virt/.pg/skills/src/runtime/bin/migrate-define-summary.py"


def _run(args, cwd=None):
    return subprocess.run(
        [sys.executable, _SCRIPT] + args,
        capture_output=True, text=True, cwd=cwd,
    )


def _make_ds(tmp, doc):
    p = os.path.join(tmp, "0-define", "define-summary.yaml")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, allow_unicode=True)
    return p


def _base_doc():
    return {
        "schema_version": 1,
        "change_id": "demo",
        "defined_at": "2026-08-05T10:00:00Z",
        "defined_by": "define",
        "target_environment": "dev-local",
        "problem": "p",
        "solution": "s",
        "boundary": {"in_scope": ["a"], "out_of_scope": ["b"]},
        "verification_needs": [
            {
                "id": "V-001",
                "name": "happy",
                "what": "w",
                "requires_capabilities": [{"capability": "postgresql", "min_quantity": 1}],
                "post_discussion_status": "verifiable",
                "env_resource_refs": [
                    "{env.infra_services[name=postgres-webvirt]}",
                ],
            },
            {
                "id": "V-002",
                "name": "degraded",
                "what": "w",
                "requires_capabilities": [{"capability": "redis_cache", "min_quantity": 1}],
                "downgrade_when_missing": "mock",
                "post_discussion_status": "degraded",
            },
        ],
    }


@unittest.skipIf(yaml is None, "PyYAML required")
class TestMigrateDefineSummary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pg-migrate-ds-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_old_format_rewrite_with_track_arg(self):
        doc = _base_doc()
        _make_ds(self.tmp, doc)
        r = _run([self.tmp, "--track", "backend"], cwd=self.tmp)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        # 读回
        out = yaml.safe_load(open(os.path.join(self.tmp, "0-define/define-summary.yaml")).read())
        self.assertEqual(out["verification_needs"][0]["id"], "V-backend-001")
        self.assertEqual(out["verification_needs"][0]["track_id"], "backend")
        self.assertEqual(out["verification_needs"][1]["id"], "V-backend-002")
        self.assertEqual(out["verification_needs"][1]["track_id"], "backend")

    def test_no_track_arg_inferred_from_existing(self):
        """doc 中已有 track_id → 推断成功, 无需 --track."""
        doc = _base_doc()
        doc["verification_needs"][0]["track_id"] = "scr"
        _make_ds(self.tmp, doc)
        r = _run([self.tmp], cwd=self.tmp)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        out = yaml.safe_load(open(os.path.join(self.tmp, "0-define/define-summary.yaml")).read())
        self.assertEqual(out["verification_needs"][0]["id"], "V-scr-001")
        self.assertEqual(out["verification_needs"][0]["track_id"], "scr")

    def test_no_track_no_existing_fails(self):
        """无 --track + 无 track_id → exit 2."""
        doc = _base_doc()
        _make_ds(self.tmp, doc)
        r = _run([self.tmp], cwd=self.tmp)
        self.assertEqual(r.returncode, 2)
        self.assertIn("无法推断 track_id", r.stderr)

    def test_dry_run_no_write(self):
        doc = _base_doc()
        _make_ds(self.tmp, doc)
        r = _run([self.tmp, "--track", "backend", "--dry-run"], cwd=self.tmp)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        # 文件未改
        out = yaml.safe_load(open(os.path.join(self.tmp, "0-define/define-summary.yaml")).read())
        self.assertEqual(out["verification_needs"][0]["id"], "V-001")  # 仍为旧

    def test_existing_new_format_skipped(self):
        """已经 V-{track}-N 形态 → 跳过改写."""
        doc = _base_doc()
        doc["verification_needs"][0]["id"] = "V-backend-1"
        doc["verification_needs"][0]["track_id"] = "backend"
        _make_ds(self.tmp, doc)
        r = _run([self.tmp, "--track", "scr"], cwd=self.tmp)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        out = yaml.safe_load(open(os.path.join(self.tmp, "0-define/define-summary.yaml")).read())
        # id 未被改成 V-scr-1
        self.assertEqual(out["verification_needs"][0]["id"], "V-backend-1")


if __name__ == "__main__":
    unittest.main()