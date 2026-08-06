#!/usr/bin/env python3
"""v1.3: pg-gen-tasks-skeleton.py --define-summary 功能单测."""
from __future__ import annotations

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

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(_SCRIPT_DIR)


def _run(args, cwd=None):
    return subprocess.run(
        [sys.executable, os.path.join(_SCRIPTS_DIR, "pg-gen-tasks-skeleton.py")] + args,
        capture_output=True, text=True, cwd=cwd,
    )


def _write_minimal_change(change_dir: str, change: str, include_ds: bool = True,
                          ds_status_map: dict | None = None):
    os.makedirs(os.path.join(change_dir, ".pg"))
    project = {
        "schema_version": 1,
        "stages": [
            {"name": "dev", "tracks": ["backend"]},
        ],
        "tracks": {
            "backend": {"type": "standard", "code_review_enabled": True},
        },
    }
    with open(os.path.join(change_dir, ".pg/project.yaml"), "w") as f:
        yaml.safe_dump(project, f, allow_unicode=True)
    os.makedirs(os.path.join(change_dir, ".pg/changes", change, "0-define"), exist_ok=True)
    os.makedirs(os.path.join(change_dir, ".pg/changes", change, "1-propose-review"), exist_ok=True)
    # proposal.md
    with open(os.path.join(change_dir, ".pg/changes", change, "proposal.md"), "w") as f:
        f.write("# proposal\n")
    if include_ds:
        ds = {
            "schema_version": 1,
            "change_id": change,
            "defined_at": "2026-08-05T10:00:00Z",
            "defined_by": "define",
            "target_environment": "dev-local",
            "problem": "p",
            "solution": "s",
            "boundary": {"in_scope": ["a"], "out_of_scope": ["b"]},
            "verification_needs": [],
        }
        for status, ids in (ds_status_map or {}).items():
            for vid in ids:
                ds["verification_needs"].append({
                    "id": vid,
                    "track_id": "backend",
                    "name": "n",
                    "what": "w",
                    "requires_capabilities": [],
                    "post_discussion_status": status,
                })
        with open(os.path.join(change_dir, ".pg/changes", change, "0-define/define-summary.yaml"), "w") as f:
            yaml.safe_dump(ds, f, allow_unicode=True)


@unittest.skipIf(yaml is None, "PyYAML required")
class TestDefineSummaryTasksIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pg-tasks-ds-")
        self.change = "demo-change"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_gen(self, extra_args=None):
        args = [
            "--change", self.change,
            "--proposal-md", os.path.join(self.tmp, ".pg/changes", self.change, "proposal.md"),
            "--affected-tracks", "backend",
            "--environment", "dev→dev-local",
            "--selected-stages", "dev",
        ]
        if extra_args:
            args.extend(extra_args)
        return _run(args, cwd=self.tmp)

    def test_verify_section_injects_status_block(self):
        """--define-summary 提供时，verify 章节应出现 V-* 状态声明."""
        _write_minimal_change(
            self.tmp, self.change,
            ds_status_map={
                "verifiable": ["V-backend-1", "V-backend-2"],
                "degraded": ["V-backend-3"],
                "skipped": ["V-backend-4"],
            },
        )
        r = self._run_gen([
            "--define-summary",
            os.path.join(self.tmp, ".pg/changes", self.change, "0-define/define-summary.yaml"),
        ])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        tasks_path = os.path.join(self.tmp, ".pg/changes", self.change, "tasks.md")
        tasks = open(tasks_path).read()
        self.assertIn("**define-summary 对账**", tasks)
        self.assertIn("verifiable: V-backend-1, V-backend-2", tasks)
        self.assertIn("degraded: V-backend-3", tasks)
        self.assertIn("skipped: V-backend-4", tasks)

    def test_no_define_summary_no_status_block(self):
        """不指定 --define-summary 且默认路径不存在时，无状态声明."""
        _write_minimal_change(self.tmp, self.change, include_ds=False)
        r = self._run_gen()
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        tasks_path = os.path.join(self.tmp, ".pg/changes", self.change, "tasks.md")
        tasks = open(tasks_path).read()
        self.assertNotIn("define-summary 对账", tasks)

    def test_default_define_summary_path_used(self):
        """不指定 --define-summary 时，默认路径 .pg/changes/<change>/0-define/define-summary.yaml."""
        _write_minimal_change(
            self.tmp, self.change,
            ds_status_map={"verifiable": ["V-backend-1"]},
        )
        r = self._run_gen()
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        tasks_path = os.path.join(self.tmp, ".pg/changes", self.change, "tasks.md")
        tasks = open(tasks_path).read()
        self.assertIn("define-summary 对账", tasks)


if __name__ == "__main__":
    unittest.main()