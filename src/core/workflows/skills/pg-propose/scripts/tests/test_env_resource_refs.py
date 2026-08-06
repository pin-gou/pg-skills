#!/usr/bin/env python3
"""v1.3: env_resource_refs 强引用校验单测.

覆盖 pg-validate-proposal.py manifest 阶段新规则:
- define-summary 缺失 → 不报 (向后兼容旧 change)
- define-summary 中 env_resource_refs 为空 → 不报
- design.md 完全未引用 → WARN env_resource_refs_design_unused
- scenario-*.yaml 联合未引用 → WARN env_resource_refs_scenario_unused
- 正确引用 → 不报
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

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(_SCRIPT_DIR)


def _run(script_args, cwd=None, env_extra=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, os.path.join(_SCRIPTS_DIR, "pg-validate-proposal.py")]
        + script_args,
        capture_output=True, text=True, env=env, cwd=cwd,
    )


def _change_setup(change="env-ref-test"):
    tmp = tempfile.mkdtemp(prefix="pg-envref-")
    os.makedirs(os.path.join(tmp, ".pg"))
    with open(os.path.join(tmp, ".pg/project.yaml"), "w") as f:
        f.write("schema: spec-driven\n")
    os.makedirs(os.path.join(tmp, ".pg/changes", change, "0-define"))
    return tmp, change


def _write_ds(change_dir, change, doc):
    p = os.path.join(change_dir, ".pg/changes", change, "0-define", "define-summary.yaml")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        yaml.safe_dump(doc, f, allow_unicode=True)


def _write_env_desc(change_dir, change):
    ed = {
        "schema_version": 1,
        "described_by": "x",
        "described_at": "2026-08-05T10:00:00Z",
        "described_for": {"caller": "pg-propose", "change": change, "environment": "dev-local"},
        "environments": {
            "dev-local": {
                "infra_services": [{"name": "postgres-webvirt", "instances": [{"id": "pg-1"}]}],
                "data_resources": [{"name": "instance-table", "state": {"status": "seeded"}}],
            }
        },
    }
    p = os.path.join(change_dir, ".pg/changes", change, "env-description.yaml")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        yaml.safe_dump(ed, f, allow_unicode=True)


def _write_design(change_dir, change, content):
    with open(os.path.join(change_dir, ".pg/changes", change, "design.md"), "w") as f:
        f.write(content)


def _write_scenario(change_dir, change, content):
    with open(os.path.join(change_dir, ".pg/changes", change, "scenario-scr.yaml"), "w") as f:
        f.write(content)


def _write_tasks_and_manifest(change_dir, change):
    """最小 tasks.md + manifest.yaml 满足 validator 早期检查."""
    change_path = os.path.join(change_dir, ".pg/changes", change)
    os.makedirs(change_path, exist_ok=True)
    tasks = "# tasks\n\n## 1. dev.scr:test - t\n\n- 无\n"
    with open(os.path.join(change_path, "tasks.md"), "w") as f:
        f.write(tasks)
    manifest = {
        "schema_version": 1,
        "tracks": {},
        "stages": [],
    }
    with open(os.path.join(change_path, "execution-manifest.yaml"), "w") as f:
        yaml.safe_dump(manifest, f, allow_unicode=True)


def _valid_ds(refs=True):
    doc = {
        "schema_version": 1,
        "change_id": "env-ref-test",
        "defined_at": "2026-08-05T10:00:00Z",
        "defined_by": "define",
        "target_environment": "dev-local",
        "problem": "p",
        "solution": "s",
        "boundary": {"in_scope": ["a"], "out_of_scope": ["b"]},
        "verification_needs": [
            {
                "id": "V-backend-1",
                "track_id": "backend",
                "name": "n",
                "what": "w",
                "requires_capabilities": [{"capability": "postgresql", "min_quantity": 1}],
                "post_discussion_status": "verifiable",
                "env_resource_refs": [
                    "{env.infra_services[name=postgres-webvirt]}",
                    "{env.data_resources[name=instance-table]}",
                ] if refs else [],
            },
        ],
    }
    return doc


DESIGN_USING_REFS = """# design
## 环境限制与验证策略

| V-* | ok | 方法 | 备注 |
|-----|----|----|----|
| V-backend-1 | yes | unit | {env.infra_services[name=postgres-webvirt]} + {env.data_resources[name=instance-table]} |
"""

DESIGN_NOT_USING_REFS = """# design
## 环境限制与验证策略

| V-* | ok | 方法 | 备注 |
|-----|----|----|----|
| V-backend-1 | yes | unit | 无具体资源引用 |
"""

SCENARIO_USING_REFS = """scenarios:
- scenario_id: S-x
  critical: true
  description: y
  given:
  - "{env.infra_services[name=postgres-webvirt]} 可达"
  when: []
  then: []
  and: []
  evidence: []
  covers: [V-backend-1]
"""

SCENARIO_NOT_USING_REFS = """scenarios:
- scenario_id: S-x
  critical: true
  description: y
  given:
  - "无引用"
  when: []
  then: []
  and: []
  evidence: []
  covers: [V-backend-1]
"""


@unittest.skipIf(yaml is None, "PyYAML required")
class TestEnvResourceRefsValidation(unittest.TestCase):
    def setUp(self):
        self.tmp, self.change = _change_setup()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_validate(self):
        return _run(
            ["manifest", self.change],
            cwd=self.tmp,
            env_extra={"PG_PROJECT_ROOT": self.tmp},
        )

    def _full_setup(self, ds_refs, design, scenario):
        _write_ds(self.tmp, self.change, _valid_ds(refs=ds_refs))
        _write_env_desc(self.tmp, self.change)
        _write_design(self.tmp, self.change, design)
        _write_scenario(self.tmp, self.change, scenario)
        _write_tasks_and_manifest(self.tmp, self.change)

    def test_no_define_summary_no_warn(self):
        """define-summary.yaml 不存在 → 跳过本规则."""
        _write_env_desc(self.tmp, self.change)
        _write_design(self.tmp, self.change, DESIGN_NOT_USING_REFS)
        _write_scenario(self.tmp, self.change, SCENARIO_NOT_USING_REFS)
        _write_tasks_and_manifest(self.tmp, self.change)
        result = self._run_validate()
        self.assertNotIn("env_resource_refs", result.stderr)

    def test_empty_refs_in_define_summary_no_warn(self):
        """define-summary 中 env_resource_refs 为空 → 跳过 (因为无强制要求)."""
        self._full_setup(ds_refs=False, design=DESIGN_NOT_USING_REFS,
                          scenario=SCENARIO_NOT_USING_REFS)
        result = self._run_validate()
        self.assertNotIn("env_resource_refs_design_unused", result.stderr)
        self.assertNotIn("env_resource_refs_scenario_unused", result.stderr)

    def test_design_unused_warn(self):
        """define-summary 有 refs 但 design 完全未引用 → WARN."""
        self._full_setup(ds_refs=True, design=DESIGN_NOT_USING_REFS,
                          scenario=SCENARIO_USING_REFS)
        result = self._run_validate()
        self.assertIn("env_resource_refs_design_unused", result.stderr)

    def test_scenario_unused_warn(self):
        """define-summary 有 refs 但 scenario 联合未引用 → WARN."""
        self._full_setup(ds_refs=True, design=DESIGN_USING_REFS,
                          scenario=SCENARIO_NOT_USING_REFS)
        result = self._run_validate()
        self.assertIn("env_resource_refs_scenario_unused", result.stderr)

    def test_full_compliance_no_warn(self):
        """design 与 scenario 都引用了 → 不应 WARN."""
        self._full_setup(ds_refs=True, design=DESIGN_USING_REFS,
                          scenario=SCENARIO_USING_REFS)
        result = self._run_validate()
        self.assertNotIn("env_resource_refs_design_unused", result.stderr)
        self.assertNotIn("env_resource_refs_scenario_unused", result.stderr)


if __name__ == "__main__":
    unittest.main()