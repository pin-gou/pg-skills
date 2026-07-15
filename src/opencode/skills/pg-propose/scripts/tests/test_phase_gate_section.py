#!/usr/bin/env python3
"""v3.4: pg-propose verify / gate 关闭场景单测。

覆盖：
- verify_enabled=false → tasks.md 减少 verify sub，phase_prompts 4 sub
- gate_enabled=false → tasks.md 减少 gate sub，phase_prompts 4 sub
- verify_enabled=false + gate_enabled=false → phase_prompts 3 sub
- review/verify/gate 全关 → phase_prompts 2 sub (只有 test+dev)
- manifest validator 接受 2-5 sub
- 质量门强制：至少 verify 或 gate 之一存在（review 单独不算）
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(_SCRIPT_DIR)
_PROJECT_ROOT = "/home/ubuntu/workspace/oc1-web-virt"


def _run_script(script_name, args, cwd=None, env_extra=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, os.path.join(_SCRIPTS_DIR, script_name)] + args,
        capture_output=True, text=True, env=env, cwd=cwd or _PROJECT_ROOT,
    )


class _ProjectMixin:
    """共享 project.yaml 配置注入 helper。

    备份 + 修改 + 恢复，避免测试互相污染或污染真实仓库。
    """

    PROJECT_YAML = os.path.join(_PROJECT_ROOT, ".pg", "project.yaml")

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pg_test_v34_")
        self.change = getattr(self, "change_name", "test-v34-section")
        self.proposal_path = os.path.join(self.tmpdir, "proposal.md")
        with open(self.proposal_path, "w", encoding="utf-8") as f:
            f.write("Test proposal with backend changes (verify/gate toggle).")
        # 备份原 project.yaml
        backup = os.path.join(self.tmpdir, "project.yaml.bak")
        shutil.copy(self.PROJECT_YAML, backup)
        self._backup_path = backup

    def tearDown(self):
        if os.path.isfile(self._backup_path):
            shutil.copy(self._backup_path, self.PROJECT_YAML)
        change_dir = os.path.join(_PROJECT_ROOT, ".pg", "changes", self.change)
        if os.path.isdir(change_dir):
            shutil.rmtree(change_dir)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _set_track_flags(self, backend: dict | None = None):
        """修改 tracks.backend 的 *_enabled 字段。"""
        with open(self.PROJECT_YAML, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for k, v in (backend or {}).items():
            data.setdefault("tracks", {}).setdefault("backend", {})[k] = v
        with open(self.PROJECT_YAML, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)

    def _generate(self):
        r1 = _run_script("pg-gen-tasks-skeleton.py", [
            "--change", self.change,
            "--proposal-md", self.proposal_path,
            "--affected-tracks", "backend",
            "--environment", "dev→dev-local",
        ])
        if r1.returncode != 0:
            return r1, {}
        return r1, json.loads(r1.stdout or "{}")


class TestTasksSkeletonByToggle(_ProjectMixin, unittest.TestCase):
    """tasks.md 子章节数按 verify_enabled / gate_enabled 过滤。"""

    change_name = "test-v34-skeleton"

    def test_default_all_enabled_5_sub(self):
        """不设开关 → 5 sub。"""
        r, json_out = self._generate()
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        backend_subs = [
            s["sub"] for s in json_out["sections"]
            if s["track"] == "backend" and s.get("sub")
        ]
        self.assertEqual(backend_subs, ["test", "dev", "review", "verify", "gate"])

    def test_verify_disabled_4_sub(self):
        """verify_enabled=false → 4 sub（去掉 verify）。"""
        self._set_track_flags(backend={"verify_enabled": False})
        r, json_out = self._generate()
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        backend_subs = [
            s["sub"] for s in json_out["sections"]
            if s["track"] == "backend" and s.get("sub")
        ]
        self.assertEqual(backend_subs, ["test", "dev", "review", "gate"])

    def test_gate_disabled_4_sub(self):
        """gate_enabled=false → 4 sub（去掉 gate）。"""
        self._set_track_flags(backend={"gate_enabled": False})
        r, json_out = self._generate()
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        backend_subs = [
            s["sub"] for s in json_out["sections"]
            if s["track"] == "backend" and s.get("sub")
        ]
        self.assertEqual(backend_subs, ["test", "dev", "review", "verify"])

    def test_verify_and_gate_disabled_3_sub(self):
        """verify + gate 都关闭 → 3 sub。"""
        self._set_track_flags(backend={"verify_enabled": False, "gate_enabled": False})
        r, json_out = self._generate()
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        backend_subs = [
            s["sub"] for s in json_out["sections"]
            if s["track"] == "backend" and s.get("sub")
        ]
        self.assertEqual(backend_subs, ["test", "dev", "review"])

    def test_all_three_disabled_2_sub(self):
        """三个开关全关 → 只剩 test + dev。"""
        self._set_track_flags(backend={
            "code_review_enabled": False,
            "verify_enabled": False,
            "gate_enabled": False,
        })
        r, json_out = self._generate()
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        backend_subs = [
            s["sub"] for s in json_out["sections"]
            if s["track"] == "backend" and s.get("sub")
        ]
        self.assertEqual(backend_subs, ["test", "dev"])


class TestManifestByToggle(_ProjectMixin, unittest.TestCase):
    """manifest phase_prompts 与 tasks.md 同步反映开关。"""

    change_name = "test-v34-manifest"

    def test_manifest_no_verify_when_disabled(self):
        self._set_track_flags(backend={"verify_enabled": False})
        r1 = _run_script("pg-gen-tasks-skeleton.py", [
            "--change", self.change,
            "--proposal-md", self.proposal_path,
            "--affected-tracks", "backend",
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
        self.assertNotIn("verify", backend_track["phase_prompts"])
        # 4 sub：test/dev/review/gate
        self.assertEqual(len(backend_track["phase_prompts"]), 4)

    def test_manifest_no_gate_when_disabled(self):
        self._set_track_flags(backend={"gate_enabled": False})
        r1 = _run_script("pg-gen-tasks-skeleton.py", [
            "--change", self.change,
            "--proposal-md", self.proposal_path,
            "--affected-tracks", "backend",
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
        self.assertNotIn("gate", backend_track["phase_prompts"])
        self.assertEqual(len(backend_track["phase_prompts"]), 4)


class TestValidatorAcceptsSubset(_ProjectMixin, unittest.TestCase):
    """v3.4: pg-validate-proposal.py 接受 2-5 sub（含质量门最少 1 项约束）。"""

    change_name = "test-v34-validator"

    def _gen_and_validate(self, flags: dict) -> tuple[int, str, str]:
        """Generate manifest with given flags and run validator."""
        self._set_track_flags(backend=flags)
        r1 = _run_script("pg-gen-tasks-skeleton.py", [
            "--change", self.change,
            "--proposal-md", self.proposal_path,
            "--affected-tracks", "backend",
            "--environment", "dev→dev-local",
        ])
        if r1.returncode != 0:
            return r1.returncode, r1.stdout, r1.stderr
        r2 = _run_script("pg-gen-manifest.py", [self.change])
        if r2.returncode != 0:
            return r2.returncode, r2.stdout, r2.stderr
        # 创建 dummy scenario.yaml（project.yaml 有 scenario-test track，validator 会检查）
        change_dir = os.path.join(_PROJECT_ROOT, ".pg", "changes", self.change)
        scenario_yaml = os.path.join(change_dir, "scenario.yaml")
        if not os.path.isfile(scenario_yaml):
            with open(scenario_yaml, "w") as f:
                f.write("scenarios: []\n")
        r3 = _run_script("pg-validate-proposal.py", ["manifest", self.change])
        return r3.returncode, r3.stdout, r3.stderr

    def test_validator_passes_4_sub_no_verify(self):
        rc, stdout, stderr = self._gen_and_validate({"verify_enabled": False})
        self.assertEqual(rc, 0, msg=f"stdout={stdout!r} stderr={stderr!r}")
        self.assertIn("OK", stdout)

    def test_validator_passes_4_sub_no_gate(self):
        rc, stdout, stderr = self._gen_and_validate({"gate_enabled": False})
        self.assertEqual(rc, 0, msg=f"stdout={stdout!r} stderr={stderr!r}")
        self.assertIn("OK", stdout)

    def test_validator_rejects_3_sub_both_disabled(self):
        """verify + gate 全关只留 review：review 不算质量门，validator 必须拒绝。"""
        rc, stdout, stderr = self._gen_and_validate({
            "verify_enabled": False, "gate_enabled": False,
        })
        # validator 应报 _no_quality_gate（review alone is not a quality gate）
        self.assertNotEqual(rc, 0, msg=f"expected non-zero, got rc={rc}")
        self.assertIn("no_quality_gate", stderr + stdout)


class TestValidatorRequiresQualityGate(_ProjectMixin, unittest.TestCase):
    """v3.4: 全 5 sub 关闭仅留 test+dev → validator 拒绝（无质量门）。"""

    change_name = "test-v34-no-quality-gate"

    def test_no_quality_gate_rejected(self):
        """verify+gate 同时关闭，但 review 也关 → manifest phase_prompts 只剩 test+dev。

        validate-proposal.py 应检测出 _no_quality_gate。
        """
        # 注意：此测试不能用 tasks-skeleton 自动生成——它会拒绝生成。
        # 改为手工写一份 manifest 走 validator 单测。
        rc, stdout, stderr = self._gen_and_validate_manual({
            "code_review_enabled": False,
            "verify_enabled": False,
            "gate_enabled": False,
        })
        # tasks-skeleton 在 tasks.md 阶段也会报缺 sub，但 manifest 也应能写出
        # 关键：validator 必须拒绝。
        self.assertNotEqual(rc, 0, msg=f"expected non-zero, got rc={rc}")

    def _gen_and_validate_manual(self, flags):
        """手工构造 manifest 测 validator（绕过 tasks-skeleton）。"""
        # 1) 临时保留所有开关启用以让 tasks-skeleton 跑通
        self._set_track_flags(backend={
            "code_review_enabled": True,
            "verify_enabled": True,
            "gate_enabled": True,
        })
        r1 = _run_script("pg-gen-tasks-skeleton.py", [
            "--change", self.change,
            "--proposal-md", self.proposal_path,
            "--affected-tracks", "backend",
            "--environment", "dev→dev-local",
        ])
        if r1.returncode != 0:
            return r1.returncode, r1.stdout, r1.stderr
        # 2) 手工改 manifest 砍掉 review/verify/gate（保留 test+dev）
        manifest_path = os.path.join(
            _PROJECT_ROOT, ".pg", "changes", self.change, "execution-manifest.yaml",
        )
        _ = _run_script("pg-gen-manifest.py", [self.change])
        with open(manifest_path) as f:
            manifest = yaml.safe_load(f)
        for stage in manifest["stages"]:
            for trk in stage["tracks"]:
                if trk["id"] == "backend":
                    pp = trk.get("phase_prompts", {})
                    for sub in ("review", "verify", "gate"):
                        pp.pop(sub, None)
                    trk["phase_prompts"] = pp
        with open(manifest_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(manifest, f, default_flow_style=False, allow_unicode=True)
        # 创建 dummy scenario.yaml（validator 检查）
        change_dir = os.path.join(_PROJECT_ROOT, ".pg", "changes", self.change)
        scenario_yaml = os.path.join(change_dir, "scenario.yaml")
        if not os.path.isfile(scenario_yaml):
            with open(scenario_yaml, "w") as f:
                f.write("scenarios: []\n")
        # 3) 跑 validator：应有 _no_quality_gate 错误
        r3 = _run_script("pg-validate-proposal.py", ["manifest", self.change])
        return r3.returncode, r3.stdout, r3.stderr


if __name__ == "__main__":
    unittest.main()
