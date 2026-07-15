#!/usr/bin/env python3
"""v3.3: pg-propose review 章节生成 + 4/5 sub 动态行为单测。

覆盖：
- code_review_enabled=true → 5 sub（test/dev/review/verify/gate）
- code_review_enabled=false → 4 sub（test/dev/verify/gate）
- 章节号 N 顺序递增（含 final-gate）
- review 章节 body 预填 placeholder
- pg-gen-manifest.py 输出 phase_prompts 含/不含 review
- manifest.schema.json 4-5 sub 都通过校验
- pg-validate-proposal.py 不报错
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

import yaml

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(_SCRIPT_DIR)
_PROJECT_ROOT = "/home/ubuntu/workspace/oc1-web-virt"


def _run_script(script_name, args, cwd=None, env_extra=None):
    """Run a pg-propose script and return CompletedProcess."""
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, os.path.join(_SCRIPTS_DIR, script_name)] + args,
        capture_output=True, text=True, env=env, cwd=cwd or _PROJECT_ROOT,
    )


def _write_yaml(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


class TestCodeViewSectionDynamic(unittest.TestCase):
    """v3.3: code-review_enabled 决定 4/5 sub 动态行为。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pg_test_cv_")
        self.change = "test-cv-section"

    def tearDown(self):
        # cleanup change dir in project
        change_dir = os.path.join(_PROJECT_ROOT, ".pg", "changes", self.change)
        if os.path.isdir(change_dir):
            shutil.rmtree(change_dir)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _setup_project(self, backend_enabled=True, frontend_enabled=True):
        """写一份 project.yaml 到 .pg/project.yaml 临时区域，注入测试配置。"""
        # 备份原 project.yaml
        backup = os.path.join(self.tmpdir, "project.yaml.bak")
        orig = os.path.join(_PROJECT_ROOT, ".pg", "project.yaml")
        shutil.copy(orig, backup)
        with open(orig, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        # 修改 tracks.backend / tracks.frontend 的 code_review_enabled
        for track_id, enabled in [
            ("backend", backend_enabled),
            ("frontend", frontend_enabled),
        ]:
            if track_id in data.get("tracks", {}):
                data["tracks"][track_id]["code_review_enabled"] = enabled
        with open(orig, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)
        # 准备 proposal.md
        self.proposal_path = os.path.join(self.tmpdir, "proposal.md")
        with open(self.proposal_path, "w", encoding="utf-8") as f:
            f.write("Test proposal with backend + frontend changes.")

    def tearDown_extra(self):
        # 恢复 project.yaml
        backup = os.path.join(self.tmpdir, "project.yaml.bak")
        orig = os.path.join(_PROJECT_ROOT, ".pg", "project.yaml")
        if os.path.isfile(backup):
            shutil.copy(backup, orig)

    def test_both_enabled_5_sub(self):
        """backend + frontend 都 code_review_enabled=true → 5 sub each。"""
        self._setup_project(backend_enabled=True, frontend_enabled=True)
        result = _run_script("pg-gen-tasks-skeleton.py", [
            "--change", self.change,
            "--proposal-md", self.proposal_path,
            "--affected-tracks", "backend,frontend",
            "--environment", "dev→dev-local",
        ])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        json_out = json.loads(result.stdout)
        # backend 5 + frontend 5 + scenario-test 2 + final-gate 1 = 13
        self.assertEqual(json_out["section_count"], 13)
        # 检查 review 在 sections 中出现 2 次
        cv_count = sum(1 for s in json_out["sections"] if s.get("sub") == "review")
        self.assertEqual(cv_count, 2)
        self.tearDown_extra()

    def test_backend_disabled_4_sub(self):
        """backend code_review_enabled=false → backend 4 sub, frontend 5 sub。"""
        self._setup_project(backend_enabled=False, frontend_enabled=True)
        result = _run_script("pg-gen-tasks-skeleton.py", [
            "--change", self.change,
            "--proposal-md", self.proposal_path,
            "--affected-tracks", "backend,frontend",
            "--environment", "dev→dev-local",
        ])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        json_out = json.loads(result.stdout)
        # backend 4 + frontend 5 + scenario-test 2 + final-gate 1 = 12
        self.assertEqual(json_out["section_count"], 12)
        # review 只出现 1 次（frontend）
        cv_sections = [s for s in json_out["sections"] if s.get("sub") == "review"]
        self.assertEqual(len(cv_sections), 1)
        self.assertEqual(cv_sections[0]["track"], "frontend")
        # backend 没有 review
        backend_subs = [
            s["sub"] for s in json_out["sections"]
            if s["track"] == "backend" and s.get("sub")
        ]
        self.assertNotIn("review", backend_subs)
        self.assertEqual(backend_subs, ["test", "dev", "verify", "gate"])
        self.tearDown_extra()

    def test_code_view_placeholder_body(self):
        """review 章节 body 预填 placeholder（4 行任务）。"""
        self._setup_project(backend_enabled=True, frontend_enabled=False)
        result = _run_script("pg-gen-tasks-skeleton.py", [
            "--change", self.change,
            "--proposal-md", self.proposal_path,
            "--affected-tracks", "backend",
            "--environment", "dev→dev-local",
        ])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        tasks_path = os.path.join(
            _PROJECT_ROOT, ".pg", "changes", self.change, "tasks.md",
        )
        with open(tasks_path) as f:
            text = f.read()
        # 包含 review 章节
        self.assertIn("## 3. dev.backend:review", text)
        # placeholder 4 行
        self.assertIn("review agent 读 design.md", text)
        self.assertIn("git diff", text)
        self.assertIn("review_score", text)
        self.assertIn("escalate 至 fix-review", text)
        self.tearDown_extra()

    def test_section_numbering_sequential(self):
        """章节号 N 顺序递增（含 final-gate）。"""
        self._setup_project(backend_enabled=True, frontend_enabled=True)
        result = _run_script("pg-gen-tasks-skeleton.py", [
            "--change", self.change,
            "--proposal-md", self.proposal_path,
            "--affected-tracks", "backend,frontend",
            "--environment", "dev→dev-local",
        ])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        json_out = json.loads(result.stdout)
        ns = [s["n"] for s in json_out["sections"]]
        # 1..13（backend 5 + frontend 5 + scenario-test 2 + final-gate 1）
        self.assertEqual(ns, list(range(1, 14)))
        self.tearDown_extra()


class TestManifestCodeView(unittest.TestCase):
    """v3.3: pg-gen-manifest.py 输出含/不含 review sub。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pg_test_cv_")
        self.change = "test-cv-manifest"
        self.proposal_path = os.path.join(self.tmpdir, "proposal.md")
        with open(self.proposal_path, "w", encoding="utf-8") as f:
            f.write("Test proposal.")
        # 备份 project.yaml
        backup = os.path.join(self.tmpdir, "project.yaml.bak")
        orig = os.path.join(_PROJECT_ROOT, ".pg", "project.yaml")
        shutil.copy(orig, backup)

    def tearDown(self):
        # 恢复 project.yaml
        backup = os.path.join(self.tmpdir, "project.yaml.bak")
        orig = os.path.join(_PROJECT_ROOT, ".pg", "project.yaml")
        if os.path.isfile(backup):
            shutil.copy(backup, orig)
        # 清理 change dir
        change_dir = os.path.join(_PROJECT_ROOT, ".pg", "changes", self.change)
        if os.path.isdir(change_dir):
            shutil.rmtree(change_dir)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _set_track_code_review(self, **kwargs):
        orig = os.path.join(_PROJECT_ROOT, ".pg", "project.yaml")
        with open(orig, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for track_id, enabled in kwargs.items():
            if track_id in data.get("tracks", {}):
                data["tracks"][track_id]["code_review_enabled"] = enabled
        with open(orig, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)

    def test_manifest_includes_code_view_when_enabled(self):
        self._set_track_code_review(backend=True, frontend=True)
        # 先生成 tasks.md
        r1 = _run_script("pg-gen-tasks-skeleton.py", [
            "--change", self.change,
            "--proposal-md", self.proposal_path,
            "--affected-tracks", "backend,frontend",
            "--environment", "dev→dev-local",
        ])
        self.assertEqual(r1.returncode, 0, msg=r1.stderr)
        # 再生成 manifest
        r2 = _run_script("pg-gen-manifest.py", [self.change])
        self.assertEqual(r2.returncode, 0, msg=r2.stderr)
        manifest_path = os.path.join(
            _PROJECT_ROOT, ".pg", "changes", self.change, "execution-manifest.yaml",
        )
        with open(manifest_path) as f:
            manifest = yaml.safe_load(f)
        # 检查 backend / frontend 都含 review sub
        backend_track = next(
            t for stage in manifest["stages"]
            for t in stage["tracks"] if t["id"] == "backend"
        )
        frontend_track = next(
            t for stage in manifest["stages"]
            for t in stage["tracks"] if t["id"] == "frontend"
        )
        self.assertIn("review", backend_track["phase_prompts"])
        self.assertIn("review", frontend_track["phase_prompts"])
        # phase_prompts 5 个 sub
        self.assertEqual(len(backend_track["phase_prompts"]), 5)

    def test_manifest_excludes_code_view_when_disabled(self):
        self._set_track_code_review(backend=False, frontend=True)
        r1 = _run_script("pg-gen-tasks-skeleton.py", [
            "--change", self.change,
            "--proposal-md", self.proposal_path,
            "--affected-tracks", "backend,frontend",
            "--environment", "dev→dev-local",
        ])
        self.assertEqual(r1.returncode, 0, msg=r1.stderr)
        r2 = _run_script("pg-gen-manifest.py", [self.change])
        self.assertEqual(r2.returncode, 0, msg=r2.stderr)
        manifest_path = os.path.join(
            _PROJECT_ROOT, ".pg", "changes", self.change, "execution-manifest.yaml",
        )
        with open(manifest_path) as f:
            manifest = yaml.safe_load(f)
        backend_track = next(
            t for stage in manifest["stages"]
            for t in stage["tracks"] if t["id"] == "backend"
        )
        frontend_track = next(
            t for stage in manifest["stages"]
            for t in stage["tracks"] if t["id"] == "frontend"
        )
        # backend 4 sub, frontend 5 sub
        self.assertNotIn("review", backend_track["phase_prompts"])
        self.assertEqual(len(backend_track["phase_prompts"]), 4)
        self.assertIn("review", frontend_track["phase_prompts"])
        self.assertEqual(len(frontend_track["phase_prompts"]), 5)


class TestValidatorCodeView(unittest.TestCase):
    """v3.3: pg-validate-proposal.py 校验 4/5 sub 都通过。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pg_test_cv_")
        self.change = "test-cv-validator"
        self.proposal_path = os.path.join(self.tmpdir, "proposal.md")
        with open(self.proposal_path, "w", encoding="utf-8") as f:
            f.write("Test proposal.")
        backup = os.path.join(self.tmpdir, "project.yaml.bak")
        orig = os.path.join(_PROJECT_ROOT, ".pg", "project.yaml")
        shutil.copy(orig, backup)

    def tearDown(self):
        backup = os.path.join(self.tmpdir, "project.yaml.bak")
        orig = os.path.join(_PROJECT_ROOT, ".pg", "project.yaml")
        if os.path.isfile(backup):
            shutil.copy(backup, orig)
        change_dir = os.path.join(_PROJECT_ROOT, ".pg", "changes", self.change)
        if os.path.isdir(change_dir):
            shutil.rmtree(change_dir)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _set_track_code_review(self, **kwargs):
        orig = os.path.join(_PROJECT_ROOT, ".pg", "project.yaml")
        with open(orig, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for track_id, enabled in kwargs.items():
            if track_id in data.get("tracks", {}):
                data["tracks"][track_id]["code_review_enabled"] = enabled
        with open(orig, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)

    def test_validator_passes_5_sub(self):
        self._set_track_code_review(backend=True, frontend=True)
        _run_script("pg-gen-tasks-skeleton.py", [
            "--change", self.change,
            "--proposal-md", self.proposal_path,
            "--affected-tracks", "backend,frontend",
            "--environment", "dev→dev-local",
            "--scenario-test-enabled", "false",
        ])
        _run_script("pg-gen-manifest.py", [self.change])
        r = _run_script("pg-validate-proposal.py", ["manifest", self.change])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("OK", r.stdout)

    def test_validator_passes_4_sub(self):
        self._set_track_code_review(backend=False, frontend=True)
        _run_script("pg-gen-tasks-skeleton.py", [
            "--change", self.change,
            "--proposal-md", self.proposal_path,
            "--affected-tracks", "backend,frontend",
            "--environment", "dev→dev-local",
            "--scenario-test-enabled", "false",
        ])
        _run_script("pg-gen-manifest.py", [self.change])
        r = _run_script("pg-validate-proposal.py", ["manifest", self.change])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("OK", r.stdout)

    def _create_dummy_scenario_yaml(self):
        change_dir = os.path.join(_PROJECT_ROOT, ".pg", "changes", self.change)
        scenario_yaml = os.path.join(change_dir, "scenario.yaml")
        if not os.path.isfile(scenario_yaml):
            with open(scenario_yaml, "w") as f:
                f.write("scenarios: []\n")


if __name__ == "__main__":
    unittest.main()
