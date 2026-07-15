"""三产物一致性测试（v3.6）。

覆盖:
- pg-gen-tasks-skeleton.py: --scenario-decisions 决定 tasks.md 是否含 scenario 章节
- pg-gen-manifest.py: 从 on-conditions-eval.md 读 SSOT 决定 manifest 是否含 scenario track
- pg-gen-scenario.py: 遍历启用 track 生成 scenario-<track>.yaml
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

CONFIG_MULTI_TRACK = {
    "stages": [
        {"name": "dev", "tracks": ["backend", "frontend"]},
        {"name": "real-integration", "tracks": ["scenario-e2e", "scenario-perf"]},
    ],
    "tracks": {
        "backend": {"modules": ["backend"]},
        "frontend": {"modules": ["frontend"]},
        "scenario-e2e": {"type": "scenario", "modules": ["backend", "frontend"]},
        "scenario-perf": {"type": "scenario", "modules": ["backend"]},
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


class TestScenarioDecisions(unittest.TestCase):
    """_compute_scenario_decisions 按 track 返回决策。"""

    def test_single_track_enabled(self):
        d = _skel._compute_scenario_decisions(CONFIG, "scenario-test=true", "需要跨模块联调")
        self.assertIn("scenario-test", d)
        self.assertEqual(d["scenario-test"]["enabled"], True)
        self.assertEqual(d["scenario-test"]["mode"], "explicit")

    def test_single_track_disabled(self):
        d = _skel._compute_scenario_decisions(CONFIG, "scenario-test=false", "纯 API 改动")
        self.assertIn("scenario-test", d)
        self.assertEqual(d["scenario-test"]["enabled"], False)
        self.assertEqual(d["scenario-test"]["mode"], "explicit")

    def test_single_track_auto(self):
        d = _skel._compute_scenario_decisions(CONFIG, "", "")
        self.assertIn("scenario-test", d)
        self.assertEqual(d["scenario-test"]["enabled"], True)
        self.assertEqual(d["scenario-test"]["mode"], "auto")

    def test_no_scenario_tracks(self):
        cfg = {"stages": [], "tracks": {"backend": {}}}
        d = _skel._compute_scenario_decisions(cfg, "", "")
        self.assertEqual(d, {})

    def test_multi_track_mixed_decisions(self):
        d = _skel._compute_scenario_decisions(
            CONFIG_MULTI_TRACK,
            "scenario-e2e=true,scenario-perf=false",
            "e2e 需要联调, perf 不需要",
        )
        self.assertEqual(d["scenario-e2e"]["enabled"], True)
        self.assertEqual(d["scenario-perf"]["enabled"], False)
        self.assertEqual(d["scenario-perf"]["mode"], "explicit")

    def test_multi_track_all_auto(self):
        d = _skel._compute_scenario_decisions(CONFIG_MULTI_TRACK, "", "")
        self.assertEqual(d["scenario-e2e"]["enabled"], True)
        self.assertEqual(d["scenario-e2e"]["mode"], "auto")
        self.assertEqual(d["scenario-perf"]["enabled"], True)
        self.assertEqual(d["scenario-perf"]["mode"], "auto")


class TestBuildSectionsWithDecision(unittest.TestCase):
    """build_sections 按 scenario_decisions 决定 scenario 章节。"""

    def test_enabled_true_has_scenario_sections(self):
        decisions = _skel._compute_scenario_decisions(CONFIG, "scenario-test=true", "")
        secs = _skel.build_sections(CONFIG, set(), set(), scenario_decisions=decisions)
        sc = [s for s in secs if s.get("is_scenario")]
        self.assertEqual(len(sc), 2)
        self.assertEqual([s["sub"] for s in sc], ["scenario-prepare", "scenario-execute"])

    def test_enabled_false_no_scenario_sections(self):
        decisions = _skel._compute_scenario_decisions(CONFIG, "scenario-test=false", "")
        secs = _skel.build_sections(CONFIG, set(), set(), scenario_decisions=decisions)
        sc = [s for s in secs if s.get("is_scenario")]
        self.assertEqual(len(sc), 0)

    def test_auto_follows_config(self):
        decisions = _skel._compute_scenario_decisions(CONFIG, "", "")
        secs = _skel.build_sections(CONFIG, set(), set(), scenario_decisions=decisions)
        sc = [s for s in secs if s.get("is_scenario")]
        self.assertEqual(len(sc), 2)

    def test_multi_track_partial_enable(self):
        decisions = _skel._compute_scenario_decisions(
            CONFIG_MULTI_TRACK, "scenario-e2e=true,scenario-perf=false", "",
        )
        secs = _skel.build_sections(
            CONFIG_MULTI_TRACK, set(), set(), scenario_decisions=decisions,
        )
        sc = [s for s in secs if s.get("is_scenario")]
        track_ids = {s["track"] for s in sc}
        self.assertIn("scenario-e2e", track_ids)
        self.assertNotIn("scenario-perf", track_ids)


class TestEvalMdHasDecisionSection(unittest.TestCase):
    """on-conditions-eval.md 必须含 scenario_tracks_decision 段。"""

    def test_eval_md_contains_decision(self):
        decisions = _skel._compute_scenario_decisions(CONFIG, "scenario-test=true", "需要跨模块联调")
        content = _skel.build_on_conditions_eval_md(
            CONFIG, [], "", scenario_decisions=decisions,
        )
        self.assertIn("## scenario_tracks_decision (v3.6)", content)
        self.assertIn("| scenario-test | **true** | explicit | 需要跨模块联调 |", content)

    def test_eval_md_multi_track(self):
        decisions = _skel._compute_scenario_decisions(
            CONFIG_MULTI_TRACK, "scenario-e2e=true,scenario-perf=false", "e2e 需要联调",
        )
        content = _skel.build_on_conditions_eval_md(
            CONFIG_MULTI_TRACK, [], "", scenario_decisions=decisions,
        )
        self.assertIn("scenario-e2e", content)
        self.assertIn("scenario-perf", content)


class TestManifestReadsSSOT(unittest.TestCase):
    """pg-gen-manifest.py 读 on-conditions-eval.md 的 decision (SSOT parser 单测)。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.change = "test-manifest-ssot"
        self.change_root = _make_change(self.tmp, self.change)
        decisions = _skel._compute_scenario_decisions(CONFIG, "scenario-test=true", "需要联调")
        eval_content = _skel.build_on_conditions_eval_md(
            CONFIG, [], "", scenario_decisions=decisions,
        )
        eval_path = os.path.join(
            self.change_root, "1-propose-review", "on-conditions-eval.md"
        )
        with open(eval_path, "w") as f:
            f.write(eval_content)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_read_decisions_from_absolute_path(self):
        d = _manifest._read_scenario_decisions_absolute(self.change_root)
        self.assertIsNotNone(d)
        self.assertIn("scenario-test", d)
        self.assertEqual(d["scenario-test"]["enabled"], True)
        self.assertEqual(d["scenario-test"]["reason"], "需要联调")

    def test_read_decisions_disabled(self):
        decisions = _skel._compute_scenario_decisions(CONFIG, "scenario-test=false", "纯 API")
        eval_content = _skel.build_on_conditions_eval_md(
            CONFIG, [], "", scenario_decisions=decisions,
        )
        eval_path = os.path.join(
            self.change_root, "1-propose-review", "on-conditions-eval.md"
        )
        with open(eval_path, "w") as f:
            f.write(eval_content)
        d = _manifest._read_scenario_decisions_absolute(self.change_root)
        self.assertIsNotNone(d)
        self.assertIn("scenario-test", d)
        self.assertEqual(d["scenario-test"]["enabled"], False)

    def test_read_multi_track_decisions(self):
        decisions = _skel._compute_scenario_decisions(
            CONFIG_MULTI_TRACK, "scenario-e2e=true,scenario-perf=false", "perf 不需要",
        )
        eval_content = _skel.build_on_conditions_eval_md(
            CONFIG_MULTI_TRACK, [], "", scenario_decisions=decisions,
        )
        eval_path = os.path.join(
            self.change_root, "1-propose-review", "on-conditions-eval.md"
        )
        with open(eval_path, "w") as f:
            f.write(eval_content)
        d = _manifest._read_scenario_decisions_absolute(self.change_root)
        self.assertIsNotNone(d)
        self.assertEqual(d["scenario-e2e"]["enabled"], True)
        self.assertEqual(d["scenario-perf"]["enabled"], False)


class TestValidatorThreeProductConsistency(unittest.TestCase):
    """pg-validate-proposal.py 校验三产物与 SSOT 一致。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.change = "test-validator"
        self.change_dir = _make_change(self.tmp, self.change)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_eval(self, decisions_arg: str, reason: str):
        decisions = _skel._compute_scenario_decisions(CONFIG, decisions_arg, reason)
        content = _skel.build_on_conditions_eval_md(
            CONFIG, [], "", scenario_decisions=decisions,
        )
        with open(os.path.join(self.change_dir, "1-propose-review", "on-conditions-eval.md"), "w") as f:
            f.write(content)

    def test_enabled_with_yaml_passes(self):
        self._write_eval("scenario-test=true", "需要联调")
        manifest = {
            "stages": [{
                "name": "real-integration", "environment": "dev-local",
                "tracks": [{
                    "id": "scenario-test", "type": "scenario",
                    "enabled": True, "reason": "需要联调",
                }],
            }],
        }
        # scenario-scenario-test.yaml 存在
        with open(os.path.join(self.change_dir, "scenario-scenario-test.yaml"), "w") as f:
            f.write("scenarios: []\n")
        issues = _validator._validate_three_product_consistency(manifest, self.change_dir)
        self.assertEqual([], issues, f"unexpected issues: {issues}")

    def test_disabled_no_yaml_passes(self):
        self._write_eval("scenario-test=false", "纯 API")
        manifest = {
            "stages": [{
                "name": "real-integration", "environment": "dev-local",
                "tracks": [{
                    "id": "scenario-test", "type": "scenario",
                    "enabled": False, "reason": "纯 API",
                }],
            }],
        }
        issues = _validator._validate_three_product_consistency(manifest, self.change_dir)
        self.assertEqual([], issues, f"unexpected issues: {issues}")

    def test_enabled_missing_yaml(self):
        self._write_eval("scenario-test=true", "需要联调")
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
        self._write_eval("scenario-test=false", "纯 API")
        manifest = {
            "stages": [{
                "name": "real-integration", "environment": "dev-local",
                "tracks": [{
                    "id": "scenario-test", "type": "scenario",
                    "enabled": False, "reason": "纯 API",
                }],
            }],
        }
        with open(os.path.join(self.change_dir, "scenario-scenario-test.yaml"), "w") as f:
            f.write("scenarios: []\n")
        issues = _validator._validate_three_product_consistency(manifest, self.change_dir)
        codes = [c for c, _ in issues]
        self.assertIn("scenario_yaml_should_not_exist", codes)

    def test_yaml_orphan(self):
        self._write_eval("scenario-test=false", "纯 API")
        manifest = {
            "stages": [{
                "name": "dev", "environment": "dev-local",
                "tracks": [{
                    "id": "backend", "type": "standard",
                    "enabled": True, "reason": "test",
                }],
            }],
        }
        with open(os.path.join(self.change_dir, "scenario-unknown.yaml"), "w") as f:
            f.write("scenarios: []\n")
        issues = _validator._validate_three_product_consistency(manifest, self.change_dir)
        codes = [c for c, _ in issues]
        self.assertIn("scenario_yaml_orphan", codes)


if __name__ == "__main__":
    unittest.main()