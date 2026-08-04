#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pg-quick-build-env-capability 单元测试。"""
from __future__ import print_function

import os
import sys
import unittest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(THIS_DIR)

# 兼容 python3 直接运行；脚本文件名带连字符，用 importlib 加载
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import importlib
_ec_module = importlib.import_module("pg-quick-build-env-capability")
ec = _ec_module


class TestExtractResourceRefs(unittest.TestCase):
    """资源引用提取。"""

    def test_no_refs_returns_empty(self):
        self.assertEqual(ec._extract_resource_refs({"check": "lint pass"}), [])
        self.assertEqual(ec._extract_resource_refs({"check": ""}), [])

    def test_extracts_single_ref(self):
        v = {"check": "curl http://db-1.local/api"}
        # 文本不含 section[name=...] 形式 → 空
        self.assertEqual(ec._extract_resource_refs(v), [])

    def test_extracts_ref_from_evidence(self):
        v = {
            "check": "DB migration applies",
            "evidence": "infra_services[name=postgres].instances[0].id == pg-1",
        }
        refs = ec._extract_resource_refs(v)
        self.assertEqual(refs, [("infra_services", "postgres")])

    def test_extracts_multiple_refs(self):
        v = {
            "check": "verify both",
            "evidence": (
                "infra_services[name=db].instances[0].id\n"
                "business_systems[name=svc].endpoints[0].url"
            ),
        }
        refs = ec._extract_resource_refs(v)
        self.assertEqual(
            refs,
            [("infra_services", "db"), ("business_systems", "svc")],
        )


class TestCheckResourceReachable(unittest.TestCase):
    """单资源可达性判定。"""

    def test_empty_resource(self):
        ok, reason = ec._check_resource_reachable(None)
        self.assertFalse(ok)
        self.assertEqual(reason, "resource_not_found")

    def test_empty_dict_resource(self):
        # 空 dict 是 falsy，视为资源不存在
        ok, reason = ec._check_resource_reachable({})
        self.assertFalse(ok)
        self.assertEqual(reason, "resource_not_found")

    def test_dict_without_state(self):
        ok, reason = ec._check_resource_reachable({"name": "foo"})
        self.assertFalse(ok)
        self.assertEqual(reason, "state_unknown")

    def test_state_status_ready(self):
        ok, reason = ec._check_resource_reachable(
            {"state": {"status": "ready"}}
        )
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_state_status_seeded(self):
        ok, _ = ec._check_resource_reachable({"state": {"status": "seeded"}})
        self.assertTrue(ok)

    def test_state_status_missing(self):
        ok, reason = ec._check_resource_reachable(
            {"state": {"status": "missing"}}
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "state_missing")

    def test_state_status_partial(self):
        ok, reason = ec._check_resource_reachable(
            {"state": {"status": "partial"}}
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "state_partial")


class TestEvaluate(unittest.TestCase):
    """整体评估函数。"""

    ENV_OK = {
        "infra_services": {
            "postgres": {"state": {"status": "ready"}},
            "redis": {"state": {"status": "configured"}},
        },
        "business_systems": {
            "iam": {
                "state": {"status": "ready"},
                "endpoints": [{"url": "http://iam.local"}],
            }
        },
    }

    def test_empty_verifications_returns_empty(self):
        result = ec.evaluate(self.ENV_OK, [])
        self.assertEqual(result["verifiable_v"], [])
        self.assertEqual(result["unverifiable_v"], [])
        self.assertEqual(result["issues"], [])

    def test_no_refs_verifiable(self):
        v_list = [{"id": "V-1", "check": "lint pass"}]
        result = ec.evaluate(self.ENV_OK, v_list)
        self.assertEqual(result["verifiable_v"], ["V-1"])
        self.assertEqual(result["unverifiable_v"], [])

    def test_single_ref_reachable(self):
        v_list = [{
            "id": "V-1",
            "check": "...",
            "evidence": "infra_services[name=postgres].instances[0].id",
        }]
        result = ec.evaluate(self.ENV_OK, v_list)
        self.assertEqual(result["verifiable_v"], ["V-1"])
        self.assertEqual(result["unverifiable_v"], [])

    def test_single_ref_unreachable(self):
        v_list = [{
            "id": "V-1",
            "check": "...",
            "evidence": "infra_services[name=mysql].instances[0].id",
        }]
        result = ec.evaluate(self.ENV_OK, v_list)
        self.assertEqual(result["verifiable_v"], [])
        self.assertEqual(result["unverifiable_v"], ["V-1"])
        self.assertEqual(len(result["issues"]), 1)
        self.assertEqual(result["issues"][0]["resource_ref"],
                         "infra_services[name=mysql]")

    def test_one_blocked_blocks_whole_v(self):
        """V-* 引用多个资源，任一不可达 → 整条 unverifiable。"""
        v_list = [{
            "id": "V-1",
            "check": "...",
            "evidence": (
                "infra_services[name=postgres].instances[0].id\n"
                "infra_services[name=mysql].instances[0].id"
            ),
        }]
        result = ec.evaluate(self.ENV_OK, v_list)
        self.assertEqual(result["verifiable_v"], [])
        self.assertEqual(result["unverifiable_v"], ["V-1"])
        self.assertEqual(len(result["issues"]), 1)

    def test_partial_state_is_unreachable(self):
        env = {
            "infra_services": {
                "postgres": {"state": {"status": "partial"}},
            }
        }
        v_list = [{
            "id": "V-1",
            "evidence": "infra_services[name=postgres].instances[0].id",
        }]
        result = ec.evaluate(env, v_list)
        self.assertEqual(result["unverifiable_v"], ["V-1"])

    def test_empty_env_blocks_all_refs(self):
        v_list = [{
            "id": "V-1",
            "evidence": "infra_services[name=postgres].instances[0].id",
        }]
        result = ec.evaluate({}, v_list)
        self.assertEqual(result["unverifiable_v"], ["V-1"])

    def test_none_env_treated_as_empty(self):
        result = ec.evaluate(None, [
            {"id": "V-1", "evidence": "infra_services[name=y]"}
        ])
        self.assertEqual(result["unverifiable_v"], ["V-1"])

    def test_unknown_v_id_skipped(self):
        v_list = [{"check": "no id here"}]
        result = ec.evaluate(self.ENV_OK, v_list)
        self.assertEqual(result["verifiable_v"], [])

    def test_mixed_verifications(self):
        v_list = [
            {"id": "V-1", "check": "lint pass"},  # 无资源引用 → 可达
            {"id": "V-2", "evidence": "infra_services[name=postgres]"},  # 可达
            {"id": "V-3", "evidence": "infra_services[name=mysql]"},  # 不可达
        ]
        result = ec.evaluate(self.ENV_OK, v_list)
        self.assertEqual(result["verifiable_v"], ["V-1", "V-2"])
        self.assertEqual(result["unverifiable_v"], ["V-3"])


class TestFilterCoversV(unittest.TestCase):
    """covers_v 过滤。"""

    def test_empty_tasks(self):
        self.assertEqual(ec.filter_covers_v([], ["V-1"]), [])

    def test_only_verify_tasks_filtered(self):
        tasks = [
            {"id": 1, "sub": "test"},
            {"id": 2, "sub": "dev"},
            {"id": 3, "sub": "verify", "covers_v": ["V-1", "V-2", "V-3"]},
        ]
        result = ec.filter_covers_v(tasks, ["V-1", "V-3"])
        # test / dev 不变
        self.assertEqual(result[0], tasks[0])
        self.assertEqual(result[1], tasks[1])
        # verify covers_v 过滤
        self.assertEqual(result[2]["covers_v"], ["V-1", "V-3"])

    def test_empty_verifiable_v_drops_all(self):
        tasks = [{"id": 1, "sub": "verify", "covers_v": ["V-1"]}]
        result = ec.filter_covers_v(tasks, [])
        self.assertEqual(result[0]["covers_v"], [])

    def test_original_tasks_not_mutated(self):
        tasks = [{"id": 1, "sub": "verify", "covers_v": ["V-1", "V-2"]}]
        ec.filter_covers_v(tasks, ["V-1"])
        # 原始 covers_v 未变
        self.assertEqual(tasks[0]["covers_v"], ["V-1", "V-2"])


if __name__ == "__main__":
    unittest.main()