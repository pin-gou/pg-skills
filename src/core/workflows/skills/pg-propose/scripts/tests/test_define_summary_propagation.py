#!/usr/bin/env python3
"""PR-B2: define-summary.yaml 三态 → 产物契约校验单测。

直接 import 调用 _check_define_summary_propagation, 避免 cmd_manifest
对 manifest / tasks.md / scenario 占位符等无关校验的强依赖。

覆盖:
- verifiable 的 V-* 出现在 scenario covers → 不报
- verifiable 的 V-* 未出现在 scenario covers → 报 define_summary_verifiable_uncovered
- degraded 的 V-* 出现在 design.md「环境限制与验证策略」段 → 不报
- degraded 的 V-* 未出现 → 报 define_summary_degraded_no_fallback
- skipped 的 V-* 出现在 proposal.md「风险和注意事项」/「未做」段 → 不报
- skipped 的 V-* 未出现 → 报 define_summary_skipped_not_in_proposal
- define-summary.yaml 不存在 → 跳过（向后兼容）
- 解析异常 → 跳过
"""

import importlib.util
import os
import sys
import tempfile
import unittest

try:
    import yaml
except ImportError:
    yaml = None

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_VALIDATOR = os.path.join(
    os.path.dirname(_SCRIPT_DIR), "pg-validate-proposal.py"
)


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "pg_validate_proposal", _VALIDATOR
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipIf(yaml is None, "PyYAML required")
class TestDefineSummaryPropagation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_validator()
        cls.check_fn = cls.mod._check_define_summary_propagation

    def _write_ds(self, tmp, change, doc):
        path = os.path.join(tmp, change, "0-define", "define-summary.yaml")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            yaml.safe_dump(doc, f, allow_unicode=True)
        return path

    def _ds(self, change="demo-change"):
        return {
            "schema_version": 1,
            "change_id": change,
            "defined_at": "2026-08-05T10:00:00Z",
            "defined_by": "define",
            "target_environment": "dev-local",
            "problem": "demo problem",
            "solution": "demo solution",
            "boundary": {"in_scope": ["a"], "out_of_scope": ["b"]},
            "verification_needs": [
                {
                    "id": "V-backend-1",
                    "track_id": "backend",
                    "name": "verifiable",
                    "what": "validate happy",
                    "requires_capabilities": [
                        {"capability": "postgresql", "min_quantity": 1},
                    ],
                    "post_discussion_status": "verifiable",
                    "env_resource_refs": [
                        "{env.infra_services[name=object-storage]}",
                    ],
                },
                {
                    "id": "V-backend-2",
                    "track_id": "backend",
                    "name": "degraded",
                    "what": "validate degraded",
                    "requires_capabilities": [
                        {"capability": "redis_cache", "min_quantity": 1},
                    ],
                    "post_discussion_status": "degraded",
                },
                {
                    "id": "V-backend-3",
                    "track_id": "backend",
                    "name": "skipped",
                    "what": "validate skipped",
                    "requires_capabilities": [
                        {"capability": "multi_tenant_data", "min_quantity": 2},
                    ],
                    "post_discussion_status": "skipped",
                },
            ],
        }

    def _design_md(self, degraded_v_ids):
        rows = "\n".join(
            "| {} | ❌ | n/a | mock 降级 |".format(v) for v in degraded_v_ids
        )
        return (
            "# design\n\n"
            "## Verification Criteria\n\n"
            "### dev-isolated backend Verification Criteria\n"
            "| ID | 验证项 | 前置 | 方法 | 预期 |\n"
            "|---|---|---|---|---|\n"
            "| V-backend-1 | test | none | curl | 200 |\n\n"
            "## 关键约束与契约\n\n"
            "### 环境限制与验证策略\n\n"
            "| 功能契约 (V-*) | dev-local 可验证 | 验证方式 | 不可验证部分的处理 |\n"
            "|---|:---:|---|---|\n"
            + (rows + "\n" if rows else "")
        )

    def _proposal_md(self, skipped_v_ids):
        body = "\n".join(
            "- {}: 本次不做 (环境不支持)".format(v) for v in skipped_v_ids
        )
        return (
            "# proposal\n\n"
            "## 风险和注意事项\n\n"
            "无\n\n"
            "## 未做\n\n"
            + (body + "\n" if body else "")
        )

    def _scenario_yaml(self, v_ids_in_covers):
        return yaml.safe_dump(
            {
                "schema_version": 1,
                "track_id": "backend",
                "scenarios": [
                    {
                        "scenario_id": "S-happy",
                        "critical": True,
                        "description": "happy path",
                        "covers": v_ids_in_covers,
                        "given": [],
                        "when": [],
                        "then": [],
                        "evidence": [],
                    },
                ],
            },
            allow_unicode=True,
        )

    def test_all_three_propagated_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            ds_path = self._write_ds(tmp, "demo-change", self._ds())
            design = self._design_md(["V-backend-2"])
            proposal = self._proposal_md(["V-backend-3"])
            sf = os.path.join(tmp, "demo-change", "scenario-backend.yaml")
            os.makedirs(os.path.dirname(sf), exist_ok=True)
            with open(sf, "w") as f:
                f.write(self._scenario_yaml(["V-backend-1"]))
            issues = self.check_fn(
                design, proposal, [sf], ds_path,
            )
            self.assertEqual(issues, [], msg=str(issues))

    def test_verifiable_not_covered_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            ds_path = self._write_ds(tmp, "demo-change", self._ds())
            design = self._design_md(["V-backend-2"])
            proposal = self._proposal_md(["V-backend-3"])
            sf = os.path.join(tmp, "demo-change", "scenario-backend.yaml")
            os.makedirs(os.path.dirname(sf), exist_ok=True)
            with open(sf, "w") as f:
                f.write(self._scenario_yaml([]))  # covers 空
            issues = self.check_fn(
                design, proposal, [sf], ds_path,
            )
            codes = [code for code, _ in issues]
            self.assertIn("define_summary_verifiable_uncovered", codes)

    def test_degraded_no_fallback_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            ds_path = self._write_ds(tmp, "demo-change", self._ds())
            design = self._design_md([])  # 没列 degraded
            proposal = self._proposal_md(["V-backend-3"])
            sf = os.path.join(tmp, "demo-change", "scenario-backend.yaml")
            os.makedirs(os.path.dirname(sf), exist_ok=True)
            with open(sf, "w") as f:
                f.write(self._scenario_yaml(["V-backend-1"]))
            issues = self.check_fn(
                design, proposal, [sf], ds_path,
            )
            codes = [code for code, _ in issues]
            self.assertIn("define_summary_degraded_no_fallback", codes)

    def test_skipped_not_in_proposal_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            ds_path = self._write_ds(tmp, "demo-change", self._ds())
            design = self._design_md(["V-backend-2"])
            proposal = self._proposal_md([])  # 没列 skipped
            sf = os.path.join(tmp, "demo-change", "scenario-backend.yaml")
            os.makedirs(os.path.dirname(sf), exist_ok=True)
            with open(sf, "w") as f:
                f.write(self._scenario_yaml(["V-backend-1"]))
            issues = self.check_fn(
                design, proposal, [sf], ds_path,
            )
            codes = [code for code, _ in issues]
            self.assertIn("define_summary_skipped_not_in_proposal", codes)

    def test_no_define_summary_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            issues = self.check_fn(
                "", "", [], "/nonexistent/path.yaml",
            )
            self.assertEqual(issues, [])

    def test_proposal_section_risk_and_notes_also_matches(self):
        """skipped V-* 在「风险和注意事项」段也算命中（不只是「未做」段）。"""
        with tempfile.TemporaryDirectory() as tmp:
            ds_path = self._write_ds(tmp, "demo-change", self._ds())
            design = self._design_md(["V-backend-2"])
            proposal = (
                "# proposal\n\n"
                "## 风险和注意事项\n\n"
                "- V-backend-3: 环境不支持多租户隔离, 留待未来迭代\n"
            )
            sf = os.path.join(tmp, "demo-change", "scenario-backend.yaml")
            os.makedirs(os.path.dirname(sf), exist_ok=True)
            with open(sf, "w") as f:
                f.write(self._scenario_yaml(["V-backend-1"]))
            issues = self.check_fn(
                design, proposal, [sf], ds_path,
            )
            self.assertEqual(issues, [], msg=str(issues))


if __name__ == "__main__":
    unittest.main()