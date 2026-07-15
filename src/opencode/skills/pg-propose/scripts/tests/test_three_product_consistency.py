"""三产物一致性测试（v3.5）。

覆盖:
- pg-gen-tasks-skeleton.py: --scenario-test-enabled 决定 tasks.md 是否含 scenario 章节
- pg-gen-manifest.py: 从 on-conditions-eval.md 读 SSOT 决定 manifest 是否含 scenario track
- pg-gen-scenario.py: enabled=true 写 skeleton, enabled=false 不写
- pg-validate-proposal.py: 校验三产物与 SSOT 一致
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, "/home/ubuntu/workspace/oc1-web-virt/.pg/skills/src/opencode/skills/pg-propose/scripts")

import importlib.util


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SCRIPTS = "/home/ubuntu/workspace/oc1-web-virt/.pg/skills/src/opencode/skills/pg-propose/scripts"
sys.path.insert(0, SCRIPTS)

_skel = _load("skel", f"{SCRIPTS}/pg-gen-tasks-skeleton.py")
_manifest = _load("manifest", f"{SCRIPTS}/pg-gen-manifest.py")
_scenario = _load("scenario", f"{SCRIPTS}/pg-gen-scenario.py")
_validator = _load("validator", f"{SCRIPTS}/pg-validate-proposal.py")


CONFIG = {
    "stages": [
        {"name": "dev", "tracks": ["backend", "frontend"]},
        {"name": "real-integration", "tracks": ["scenario-test"]},
    ],
    "tracks": {
        "backend": {"modules": ["backend"]},
        "frontend": {"modules": ["frontend"]},
        "scenario-test": {
            "type": "scenario",
            "modules": ["backend", "frontend", "agent"],
            "max_fix_retries": 5,
        },
    },
    "environments": {
        "dev-local": {"prepare_env": {"script": "/dev/null"}, "clean_env": {"script": "/dev/null"}},
    },
}


def _make_change(tmpdir, name="test-three-products"):
    change_dir = os.path.join(tmpdir, name)
    os.makedirs(change_dir, exist_ok=True)
    os.makedirs(os.path.join(change_dir, "1-propose-review"), exist_ok=True)
    return change_dir


class TestScenarioTestDecision(unittest.TestCase):
    """scenario-test 启用决策测试。"""

    def test_decision_enabled_true(self):
        d = _skel._compute_scenario_decision(CONFIG, "true", "需要跨模块联调")
        self.assertEqual(d["enabled"], True)
        self.assertEqual(d["mode"], "explicit")
        self.assertEqual(d["reason"], "需要跨模块联调")

    def test_decision_enabled_false(self):
        d = _skel._compute_scenario_decision(CONFIG, "false", "纯 API 改动")
        self.assertEqual(d["enabled"], False)
        self.assertEqual(d["mode"], "explicit")
        self.assertEqual(d["reason"], "纯 API 改动")

    def test_decision_auto_with_scenario(self):
        d = _skel._compute_scenario_decision(CONFIG, "auto", "")
        self.assertEqual(d["enabled"], True)
        self.assertEqual(d["mode"], "auto")

    def test_decision_auto_no_scenario(self):
        cfg = {"stages": [], "tracks": {"backend": {}}}
        d = _skel._compute_scenario_decision(cfg, "auto", "")
        self.assertEqual(d["enabled"], False)

    def test_decision_false_no_reason(self):
        d = _skel._compute_scenario_decision(CONFIG, "false", "")
        self.assertEqual(d["enabled"], False)
        self.assertEqual(d["reason"], "（LLM 未填写依据）")


class TestBuildSectionsWithDecision(unittest.TestCase):
    """build_sections 按 --scenario-test-enabled 决定 scenario 章节。"""

    def test_enabled_true_has_scenario_sections(self):
        secs = _skel.build_sections(CONFIG, set(), set(), scenario_test_enabled="true")
        sc = [s for s in secs if s.get("is_scenario")]
        self.assertEqual(len(sc), 2)
        self.assertEqual([s["sub"] for s in sc], ["scenario-prepare", "scenario-execute"])

    def test_enabled_false_no_scenario_sections(self):
        secs = _skel.build_sections(CONFIG, set(), set(), scenario_test_enabled="false")
        sc = [s for s in secs if s.get("is_scenario")]
        self.assertEqual(len(sc), 0)

    def test_auto_follows_config(self):
        secs = _skel.build_sections(CONFIG, set(), set(), scenario_test_enabled="auto")
        sc = [s for s in secs if s.get("is_scenario")]
        self.assertEqual(len(sc), 2)


class TestEvalMdHasDecisionSection(unittest.TestCase):
    """on-conditions-eval.md 必须含 scenario_test_decision 段。"""

    def test_eval_md_contains_decision(self):
        decision = _skel._compute_scenario_decision(CONFIG, "true", "需要跨模块联调")
        content = _skel.build_on_conditions_eval_md(
            CONFIG, [], "", scenario_decision=decision,
        )
        self.assertIn("## scenario_test_decision (v3.5)", content)
        self.assertIn("| enabled | **True** |", content)
        self.assertIn("| mode | explicit |", content)
        self.assertIn("需要跨模块联调", content)


class TestManifestReadsSSOT(unittest.TestCase):
    """pg-gen-manifest.py 读 on-conditions-eval.md 的 decision (SSOT parser 单测)。

    注意：build_manifest() 内部依赖真实 CHANGES_DIR，因此本测试只覆盖
    _read_scenario_decision 解析逻辑。完整 build_manifest 流程在 test_phase_gate_section 中
    通过 _run_script("pg-gen-manifest.py") 端到端测试。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.change = "test-manifest-ssot"
        self.change_root = _make_change(self.tmp, self.change)
        # 写 eval.md with decision
        decision = _skel._compute_scenario_decision(CONFIG, "true", "需要联调")
        eval_content = _skel.build_on_conditions_eval_md(
            CONFIG, [], "", scenario_decision=decision,
        )
        eval_path = os.path.join(
            self.change_root, "1-propose-review", "on-conditions-eval.md"
        )
        with open(eval_path, "w") as f:
            f.write(eval_content)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_read_decision_from_absolute_path(self):
        """_read_scenario_decision 接受 change 根绝对路径。"""
        d = _manifest._read_scenario_decision_absolute(self.change_root)
        self.assertIsNotNone(d)
        self.assertEqual(d["enabled"], True)
        self.assertEqual(d["reason"], "需要联调")

    def test_read_decision_disabled(self):
        decision = _skel._compute_scenario_decision(CONFIG, "false", "纯 API")
        eval_content = _skel.build_on_conditions_eval_md(
            CONFIG, [], "", scenario_decision=decision,
        )
        eval_path = os.path.join(
            self.change_root, "1-propose-review", "on-conditions-eval.md"
        )
        with open(eval_path, "w") as f:
            f.write(eval_content)
        d = _manifest._read_scenario_decision_absolute(self.change_root)
        self.assertIsNotNone(d)
        self.assertEqual(d["enabled"], False)
        self.assertEqual(d["reason"], "纯 API")


class TestValidatorThreeProductConsistency(unittest.TestCase):
    """pg-validate-proposal.py 校验三产物与 SSOT 一致。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.change = "test-validator"
        self.change_dir = _make_change(self.tmp, self.change)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_eval(self, enabled: bool, reason: str):
        decision = _skel._compute_scenario_decision(
            CONFIG, "true" if enabled else "false", reason,
        )
        content = _skel.build_on_conditions_eval_md(
            CONFIG, [], "", scenario_decision=decision,
        )
        with open(os.path.join(self.change_dir, "1-propose-review", "on-conditions-eval.md"), "w") as f:
            f.write(content)

    def test_enabled_with_yaml_passes(self):
        """enabled=true + scenario.yaml 存在 → 校验通过。"""
        self._write_eval(enabled=True, reason="需要联调")
        manifest = {
            "stages": [{
                "name": "real-integration", "environment": "dev-local",
                "tracks": [{
                    "id": "scenario-test", "type": "scenario",
                    "enabled": True, "reason": "需要联调",
                }],
            }],
        }
        # scenario.yaml 存在
        with open(os.path.join(self.change_dir, "scenario.yaml"), "w") as f:
            f.write("scenarios: []\n")
        issues = _validator._validate_three_product_consistency(manifest, self.change_dir)
        self.assertEqual([], issues, f"unexpected issues: {issues}")

    def test_disabled_no_yaml_passes(self):
        """enabled=false + scenario.yaml 不存在 → 校验通过。"""
        self._write_eval(enabled=False, reason="纯 API")
        manifest = {
            "stages": [{
                "name": "real-integration", "environment": "dev-local",
                "tracks": [{
                    "id": "scenario-test", "type": "scenario",
                    "enabled": False, "reason": "纯 API",
                }],
            }],
        }
        # scenario.yaml 不存在
        issues = _validator._validate_three_product_consistency(manifest, self.change_dir)
        self.assertEqual([], issues, f"unexpected issues: {issues}")

    def test_enabled_missing_yaml(self):
        """enabled=true 但 scenario.yaml 不存在 → scenario_yaml_missing。"""
        self._write_eval(enabled=True, reason="需要联调")
        manifest = {
            "stages": [{
                "name": "real-integration", "environment": "dev-local",
                "tracks": [{
                    "id": "scenario-test", "type": "scenario",
                    "enabled": True, "reason": "需要联调",
                }],
            }],
        }
        issues = _validator._validate_three_product_consistency(manifest, self.change_dir)
        codes = [c for c, _ in issues]
        self.assertIn("scenario_yaml_missing", codes)

    def test_disabled_yaml_exists(self):
        """enabled=false 但 scenario.yaml 存在 → scenario_yaml_should_not_exist。"""
        self._write_eval(enabled=False, reason="纯 API")
        manifest = {
            "stages": [{
                "name": "real-integration", "environment": "dev-local",
                "tracks": [{
                    "id": "scenario-test", "type": "scenario",
                    "enabled": False, "reason": "纯 API",
                }],
            }],
        }
        with open(os.path.join(self.change_dir, "scenario.yaml"), "w") as f:
            f.write("scenarios: []\n")
        issues = _validator._validate_three_product_consistency(manifest, self.change_dir)
        codes = [c for c, _ in issues]
        self.assertIn("scenario_yaml_should_not_exist", codes)

    def test_yaml_orphan(self):
        """scenario.yaml 存在但 manifest 不含 scenario track → scenario_yaml_orphan。"""
        self._write_eval(enabled=False, reason="纯 API")
        manifest = {
            "stages": [{
                "name": "dev", "environment": "dev-local",
                "tracks": [{
                    "id": "backend", "type": "standard",
                    "enabled": True, "reason": "test",
                }],
            }],
        }
        with open(os.path.join(self.change_dir, "scenario.yaml"), "w") as f:
            f.write("scenarios: []\n")
        issues = _validator._validate_three_product_consistency(manifest, self.change_dir)
        codes = [c for c, _ in issues]
        self.assertIn("scenario_yaml_orphan", codes)


if __name__ == "__main__":
    unittest.main()
