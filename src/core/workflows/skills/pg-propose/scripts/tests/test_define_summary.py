#!/usr/bin/env python3
"""v1.2: pg-validate-proposal.py define-summary 子命令单测。

覆盖 pg-1-define 阶段产物 .pg/changes/<change>/0-define/define-summary.yaml
（pg-propose 阶段 1.8 消费）的校验：

- 文件缺失 → FAIL（提示先在 pg-1-define 定界环节落盘）
- 完整合法（含 env-description 交叉校验）→ PASS
- env-description.yaml 缺失 → FAIL（env_description_missing）
- change_id 与目录名不一致 → FAIL
- change_id pattern 非法 → FAIL
- verification_needs id 重复 → FAIL
- verifiable 但 env_resource_refs 为空 → FAIL
- non-verifiable 但 env_resource_refs 非空 → FAIL
- env_resource_refs 引用未知资源 → FAIL
- env_resource_refs 格式非法 → FAIL
- target_environment 与 env-description described_for.environment 不一致 → FAIL
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


def _valid_define_summary(change_id="demo-change", target_env="dev-local"):
    return {
        "schema_version": 1,
        "change_id": change_id,
        "defined_at": "2026-08-05T10:00:00Z",
        "defined_by": "define",
        "target_environment": target_env,
        "problem": "demo problem",
        "solution": "demo solution",
        "boundary": {
            "in_scope": ["a"],
            "out_of_scope": ["b"],
        },
        "verification_needs": [
            {
                "id": "V-backend-1",
                "track_id": "backend",
                "name": "happy",
                "what": "validate happy path",
                "requires_capabilities": [
                    {"capability": "postgresql", "min_quantity": 1},
                ],
                "post_discussion_status": "verifiable",
                "env_resource_refs": [
                    "{env.infra_services[name=object-storage]}",
                    "{env.data_resources[name=bucket-catalog]}",
                ],
            },
            {
                "id": "V-backend-2",
                "track_id": "backend",
                "name": "degraded",
                "what": "validate degraded path",
                "requires_capabilities": [
                    {"capability": "multi_tenant_data", "min_quantity": 2},
                ],
                "downgrade_when_missing": "mock",
                "post_discussion_status": "degraded",
            },
        ],
    }


def _env_description(change_id="demo-change", target_env="dev-local"):
    return {
        "schema_version": 1,
        "described_by": "env-describe.sh",
        "described_at": "2026-08-05T10:00:00Z",
        "described_for": {
            "caller": "pg-propose",
            "change": change_id,
            "environment": target_env,
        },
        "environments": {
            target_env: {
                "infra_services": [
                    {
                        "name": "object-storage",
                        "type": "seaweedfs",
                        "category": "object_storage",
                        "instances": [{"id": "object-storage-1", "reachable": True}],
                        "capabilities": ["postgresql", "object_storage"],
                    },
                ],
                "data_resources": [
                    {
                        "name": "bucket-catalog",
                        "state": {"status": "seeded"},
                        "capabilities": ["multi_tenant_data"],
                    },
                    {
                        "name": "tenant-b",
                        "state": {"status": "seeded"},
                        "capabilities": ["multi_tenant_data"],
                    },
                ],
            },
        },
    }


@unittest.skipIf(yaml is None, "PyYAML required")
class TestDefineSummaryValidation(unittest.TestCase):
    """pg-validate-proposal.py define-summary 子命令。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pg-test-define-summary-")
        self.project_yaml = os.path.join(self.tmp, ".pg", "project.yaml")
        os.makedirs(os.path.dirname(self.project_yaml), exist_ok=True)
        with open(self.project_yaml, "w") as f:
            f.write("schema: spec-driven\n")
        self.changes_dir = os.path.join(self.tmp, ".pg", "changes")
        os.makedirs(self.changes_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_define_summary(self, change, doc):
        change_dir = os.path.join(self.changes_dir, change, "0-define")
        os.makedirs(change_dir, exist_ok=True)
        with open(os.path.join(change_dir, "define-summary.yaml"), "w") as f:
            yaml.safe_dump(doc, f, allow_unicode=True)

    def _write_env_description(self, change, doc):
        change_dir = os.path.join(self.changes_dir, change)
        os.makedirs(change_dir, exist_ok=True)
        with open(os.path.join(change_dir, "env-description.yaml"), "w") as f:
            yaml.safe_dump(doc, f, allow_unicode=True)

    def _run_validate(self, change):
        return _run(
            ["define-summary", change],
            cwd=self.tmp,
            env_extra={"PG_PROJECT_ROOT": self.tmp},
        )

    # ---------- 正例 ----------

    def test_valid_pass(self):
        change = "demo-change"
        self._write_define_summary(change, _valid_define_summary(change))
        self._write_env_description(change, _env_description(change))
        result = self._run_validate(change)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("OK: define-summary.yaml", result.stdout)

    # ---------- 反例 ----------

    def test_missing_file_fail(self):
        result = self._run_validate("no-such-change")
        self.assertEqual(result.returncode, 1)
        self.assertIn("define-summary.yaml 不存在", result.stderr)

    def test_failure_outputs_redefine_hint(self):
        """PR-B1: 校验失败 stderr 末尾必须包含可执行的 redefine 命令。"""
        change = "demo-change"
        self._write_define_summary(change, _valid_define_summary(change))
        self._write_env_description(change, _env_description(change))
        # 人为破坏 env_resource_refs 让校验失败
        doc = _valid_define_summary(change)
        doc["verification_needs"][0]["env_resource_refs"] = [
            "{env.infra_services[name=nonexistent]}",
        ]
        self._write_define_summary(change, doc)
        result = self._run_validate(change)
        self.assertEqual(result.returncode, 1)
        self.assertIn("/1-pg-define --redefine", result.stderr)
        self.assertIn(change, result.stderr)

    def test_missing_env_description_fail(self):
        change = "demo-change"
        self._write_define_summary(change, _valid_define_summary(change))
        result = self._run_validate(change)
        self.assertEqual(result.returncode, 1, msg=result.stdout)
        self.assertIn("env_description_missing", result.stderr)

    def test_change_id_dir_mismatch_fail(self):
        change = "demo-change"
        doc = _valid_define_summary(change_id="other-change")
        self._write_define_summary(change, doc)
        self._write_env_description(change, _env_description(change_id="other-change"))
        result = self._run_validate(change)
        self.assertEqual(result.returncode, 1)
        self.assertIn("define_summary_change_id_dir_mismatch", result.stderr)

    def test_change_id_bad_pattern_fail(self):
        change = "demo-change"
        doc = _valid_define_summary(change)
        doc["change_id"] = "Demo_Change!"
        self._write_define_summary(change, doc)
        self._write_env_description(change, _env_description(change))
        result = self._run_validate(change)
        self.assertEqual(result.returncode, 1)
        self.assertIn("define_summary_change_id_bad_pattern", result.stderr)

    def test_duplicate_v_id_fail(self):
        change = "demo-change"
        doc = _valid_define_summary(change)
        doc["verification_needs"][1]["id"] = "V-backend-1"
        self._write_define_summary(change, doc)
        self._write_env_description(change, _env_description(change))
        result = self._run_validate(change)
        self.assertEqual(result.returncode, 1)
        self.assertIn("define_summary_vn_duplicate_id", result.stderr)

    def test_verifiable_missing_refs_fail(self):
        change = "demo-change"
        doc = _valid_define_summary(change)
        doc["verification_needs"][0]["env_resource_refs"] = []
        self._write_define_summary(change, doc)
        self._write_env_description(change, _env_description(change))
        result = self._run_validate(change)
        self.assertEqual(result.returncode, 1)
        self.assertIn("define_summary_verifiable_missing_refs", result.stderr)

    def test_non_verifiable_has_refs_fail(self):
        change = "demo-change"
        doc = _valid_define_summary(change)
        doc["verification_needs"][1]["env_resource_refs"] = [
            "{env.data_resources[name=bucket-catalog]}",
        ]
        self._write_define_summary(change, doc)
        self._write_env_description(change, _env_description(change))
        result = self._run_validate(change)
        self.assertEqual(result.returncode, 1)
        self.assertIn("define_summary_non_verifiable_has_refs", result.stderr)

    def test_unknown_resource_ref_fail(self):
        change = "demo-change"
        doc = _valid_define_summary(change)
        doc["verification_needs"][0]["env_resource_refs"] = [
            "{env.infra_services[name=nonexistent]}",
        ]
        self._write_define_summary(change, doc)
        self._write_env_description(change, _env_description(change))
        result = self._run_validate(change)
        self.assertEqual(result.returncode, 1)
        self.assertIn("define_summary_ref_unknown_resource", result.stderr)

    def test_bad_ref_format_fail(self):
        change = "demo-change"
        doc = _valid_define_summary(change)
        doc["verification_needs"][0]["env_resource_refs"] = [
            "infra_services[name=object-storage]",  # 缺 {env. 前缀
        ]
        self._write_define_summary(change, doc)
        self._write_env_description(change, _env_description(change))
        result = self._run_validate(change)
        self.assertEqual(result.returncode, 1)
        self.assertIn("define_summary_ref_bad_format", result.stderr)

    def test_env_mismatch_fail(self):
        change = "demo-change"
        doc = _valid_define_summary(change, target_env="dev-local")
        self._write_define_summary(change, doc)
        # env-description 声明 environment 为 multi-tier → 不一致
        self._write_env_description(change, _env_description(change, target_env="multi-tier"))
        result = self._run_validate(change)
        self.assertEqual(result.returncode, 1)
        self.assertIn("define_summary_env_mismatch", result.stderr)

    def test_missing_required_field_fail(self):
        change = "demo-change"
        doc = _valid_define_summary(change)
        del doc["boundary"]
        self._write_define_summary(change, doc)
        self._write_env_description(change, _env_description(change))
        result = self._run_validate(change)
        self.assertEqual(result.returncode, 1)
        self.assertIn("define_summary_missing_boundary", result.stderr)

    # ---------- v1.3: V-{track}-N 编号 + track_id 必填 ----------

    def test_v_track_id_format_pass(self):
        """id=V-backend-1 + track_id=backend → OK（新格式）。"""
        change = "demo-change"
        doc = _valid_define_summary(change)
        doc["verification_needs"][0]["id"] = "V-backend-1"
        doc["verification_needs"][0]["track_id"] = "backend"
        self._write_define_summary(change, doc)
        self._write_env_description(change, _env_description(change))
        result = self._run_validate(change)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("OK: define-summary.yaml", result.stdout)

    def test_legacy_v_nnn_format_now_fail(self):
        """id=V-001 不带 track_id 的旧格式 → 必须 FAIL（强制迁移到 V-{track}-N）。

        旧 define-summary 必须先用 migrate-define-summary.py 迁移, 再走 propose 校验。
        schema regex 已收窄为 ^V-[a-z][a-z0-9-]*-\\d+(?:-[a-z0-9-]+)?$, V-001 直接被
        define_summary_vn_id_bad_pattern 拦截。
        """
        change = "demo-change"
        doc = _valid_define_summary(change)
        doc["verification_needs"][0]["id"] = "V-001"
        doc["verification_needs"][0].pop("track_id", None)
        doc["verification_needs"][1]["id"] = "V-002"
        doc["verification_needs"][1].pop("track_id", None)
        self._write_define_summary(change, doc)
        self._write_env_description(change, _env_description(change))
        result = self._run_validate(change)
        self.assertEqual(result.returncode, 1)
        self.assertIn("define_summary_vn_id_bad_pattern", result.stderr)

    def test_track_id_missing_pass(self):
        """PR-C1: track_id 字段可选, 省略时自动从 id 派生。"""
        change = "demo-change"
        doc = _valid_define_summary(change)
        doc["verification_needs"][0]["id"] = "V-backend-1"
        # 故意缺 track_id (省略)
        doc["verification_needs"][0].pop("track_id", None)
        self._write_define_summary(change, doc)
        self._write_env_description(change, _env_description(change))
        result = self._run_validate(change)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("OK: define-summary.yaml", result.stdout)

    def test_track_id_mismatch_fail(self):
        """id=V-backend-1 + track_id=frontend → 前后缀不一致 FAIL。"""
        change = "demo-change"
        doc = _valid_define_summary(change)
        doc["verification_needs"][0]["id"] = "V-backend-1"
        doc["verification_needs"][0]["track_id"] = "frontend"
        self._write_define_summary(change, doc)
        self._write_env_description(change, _env_description(change))
        result = self._run_validate(change)
        self.assertEqual(result.returncode, 1)
        self.assertIn("track_id", result.stderr)

    # ---------- v1.3 (PR-A1): V-{track_id}-{seq} 编号收紧 ----------

    def test_v_id_with_hyphen_suffix_pass(self):
        """id=V-backend-1-install-token (seq 后描述后缀) → PASS。"""
        change = "demo-change"
        doc = _valid_define_summary(change)
        doc["verification_needs"][0]["id"] = "V-backend-1-install-token"
        doc["verification_needs"][0]["track_id"] = "backend"
        self._write_define_summary(change, doc)
        self._write_env_description(change, _env_description(change))
        result = self._run_validate(change)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("OK: define-summary.yaml", result.stdout)

    def test_v_id_multi_segment_track_pass(self):
        """id=V-agent-proto-1 含连字符 track 段 → PASS。"""
        change = "demo-change"
        doc = _valid_define_summary(change)
        doc["verification_needs"][0]["id"] = "V-agent-proto-1"
        doc["verification_needs"][0]["track_id"] = "agent-proto"
        self._write_define_summary(change, doc)
        self._write_env_description(change, _env_description(change))
        result = self._run_validate(change)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_v_id_underscore_suffix_fail(self):
        """id=V-backend-1_extra 不带数字结尾 → FAIL（regex 强制 \\d+ 结尾）。"""
        change = "demo-change"
        doc = _valid_define_summary(change)
        doc["verification_needs"][0]["id"] = "V-backend-1_extra"
        doc["verification_needs"][0]["track_id"] = "backend"
        self._write_define_summary(change, doc)
        self._write_env_description(change, _env_description(change))
        result = self._run_validate(change)
        self.assertEqual(result.returncode, 1)
        self.assertIn("define_summary_vn_id_bad_pattern", result.stderr)

    # ---------- v1.4 (PR-A2): requires_capabilities 对账 ----------

    def test_capability_unsatisfied_fail(self):
        """requires_capability 在 env-description 中未声明 → FAIL。"""
        change = "demo-change"
        doc = _valid_define_summary(change)
        doc["verification_needs"][0]["requires_capabilities"] = [
            {"capability": "redis_cache", "min_quantity": 1},
        ]
        self._write_define_summary(change, doc)
        self._write_env_description(change, _env_description(change))
        result = self._run_validate(change)
        self.assertEqual(result.returncode, 1)
        self.assertIn("define_summary_capability_unsatisfied", result.stderr)

    def test_capability_quantity_insufficient_fail(self):
        """requires_capability min_quantity > 环境累计数量 → FAIL。"""
        change = "demo-change"
        doc = _valid_define_summary(change)
        doc["verification_needs"][0]["requires_capabilities"] = [
            {"capability": "postgresql", "min_quantity": 5},
        ]
        self._write_define_summary(change, doc)
        self._write_env_description(change, _env_description(change))
        result = self._run_validate(change)
        self.assertEqual(result.returncode, 1)
        self.assertIn("define_summary_capability_quantity_insufficient", result.stderr)

    def test_capability_via_infra_instances_count_pass(self):
        """infra_service 有 N 个 instances → capability 计数 = N（满足 min_quantity=N）。"""
        change = "demo-change"
        env_doc = _env_description(change)
        env_doc["environments"]["dev-local"]["infra_services"][0][
            "instances"
        ] = [{"id": f"pg-{i}", "reachable": True} for i in range(3)]
        doc = _valid_define_summary(change)
        doc["verification_needs"][0]["requires_capabilities"] = [
            {"capability": "postgresql", "min_quantity": 3},
        ]
        self._write_define_summary(change, doc)
        self._write_env_description(change, env_doc)
        result = self._run_validate(change)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_capability_via_business_system_count_pass(self):
        """business_system 声明 capability → 计数 = 1（不依赖 endpoints 数）。"""
        change = "demo-change"
        env_doc = _env_description(change)
        env_doc["environments"]["dev-local"]["business_systems"] = [
            {
                "name": "iam-server",
                "type": "rest-api",
                "category": "upstream",
                "endpoints": [{"name": "primary", "url": "http://iam:8080"}],
                "capabilities": ["iam_rbac"],
            },
        ]
        doc = _valid_define_summary(change)
        doc["verification_needs"][0]["requires_capabilities"] = [
            {"capability": "iam_rbac", "min_quantity": 1},
        ]
        self._write_define_summary(change, doc)
        self._write_env_description(change, env_doc)
        result = self._run_validate(change)
        self.assertEqual(result.returncode, 0, msg=result.stderr)


if __name__ == "__main__":
    unittest.main()
