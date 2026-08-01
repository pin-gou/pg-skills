"""v1.1.0 (P0-1) scenario 硬编码 endpoint 校验单测.

覆盖:
- 硬编码 IPv4 / ssh://user@host / http://host:port / port=9082 全部命中
- 占位符 {env.*} / 注释行 / localhost / 127.0.0.1 / port<1000 全部豁免
- 嵌套 dict / list 递归扫描
- 环境变量 PG_PROPOSE_V110_HARDCODED=0 关闭规则
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import importlib.util


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

_VAL_PATH = os.path.join(SCRIPTS, "pg-validate-proposal.py")
_val = _load("pg_validate_proposal_test", _VAL_PATH)


def _make_change_dir(tmpdir: str, scenario_yaml_text: str) -> str:
    """构造一个最小可走 _validate_three_product_consistency 的 change 目录."""
    change_root = os.path.join(tmpdir, "test-change")
    os.makedirs(change_root, exist_ok=True)
    with open(os.path.join(change_root, "scenario-scr.yaml"), "w", encoding="utf-8") as f:
        f.write(scenario_yaml_text)
    return change_root


def _run_check(change_root: str, expected_file: str = "scenario-scr.yaml",
               expected_from_manifest: set | None = None):
    if expected_from_manifest is None:
        expected_from_manifest = {expected_file}
    existing = {expected_file}
    return _val._validate_scenario_no_hardcoded_endpoint(
        change_root, existing, expected_from_manifest,
    )


class TestHardcodedEndpoint(unittest.TestCase):

    def test_ipv4_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_change_dir(tmp, """scenarios:
  - scenario_id: S-1
    given: ["192.168.122.221"]
    when: []
    then: []
""")
            issues = _run_check(root)
            self.assertTrue(any(c == "scenario_given_hardcoded_endpoint" for c, _ in issues),
                            f"ipv4 应被命中: {issues}")

    def test_ssh_user_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_change_dir(tmp, """scenarios:
  - scenario_id: S-1
    given: ["ssh://ubuntu@vm-host"]
    when: []
    then: []
""")
            issues = _run_check(root)
            self.assertTrue(any(c == "scenario_given_hardcoded_endpoint" for c, _ in issues),
                            f"ssh://user@ 应被命中: {issues}")

    def test_http_endpoint_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_change_dir(tmp, """scenarios:
  - scenario_id: S-1
    when:
      - name: call
        url: "http://10.0.0.5:9080/api/test"
""")
            issues = _run_check(root)
            self.assertTrue(any(c == "scenario_given_hardcoded_endpoint" for c, _ in issues),
                            f"http://host:port 应被命中: {issues}")

    def test_port_literal_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_change_dir(tmp, """scenarios:
  - scenario_id: S-1
    given: ["connect port 9082"]
""")
            issues = _run_check(root)
            self.assertTrue(any(c == "scenario_given_hardcoded_endpoint" for c, _ in issues),
                            f"port 字面应被命中: {issues}")

    def test_port_low_number_exempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_change_dir(tmp, """scenarios:
  - scenario_id: S-1
    given: ["connect port 80"]
""")
            issues = _run_check(root)
            port_hits = [m for c, m in issues if c == "scenario_given_hardcoded_endpoint"
                         and "port_literal" in m]
            self.assertEqual(port_hits, [], "port<1000 应被豁免")

    def test_localhost_exempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_change_dir(tmp, """scenarios:
  - scenario_id: S-1
    when:
      - url: "http://localhost:3008/dashboard"
    given: ["127.0.0.1:8080 backend"]
""")
            issues = _run_check(root)
            self.assertEqual(issues, [], "localhost/127.0.0.1 应全部豁免")

    def test_placeholder_exempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_change_dir(tmp, """scenarios:
  - scenario_id: S-1
    given:
      - "{env.infra_services[name=sandbox].instances[0].endpoint}"
      - "url: {env.business_systems[name=backend].endpoints[0].url}"
""")
            issues = _run_check(root)
            self.assertEqual(issues, [], "{env.*} 占位符应豁免")

    def test_comment_exempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_change_dir(tmp, """scenarios:
  - scenario_id: S-1
    given:
      - "# 192.168.122.221 historical IP, replaced by env placeholder"
""")
            issues = _run_check(root)
            self.assertEqual(issues, [], "注释行应豁免")

    def test_then_field_also_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_change_dir(tmp, """scenarios:
  - scenario_id: S-1
    then: ["response.host == 192.168.122.221"]
""")
            issues = _run_check(root)
            self.assertTrue(any("then" in m for c, m in issues if c == "scenario_given_hardcoded_endpoint"),
                            f"then 字段应被扫描: {issues}")

    def test_env_var_disables_rule(self):
        os.environ["PG_PROPOSE_V110_HARDCODED"] = "0"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = _make_change_dir(tmp, """scenarios:
  - scenario_id: S-1
    given: ["192.168.122.221"]
""")
                issues = _run_check(root)
                self.assertEqual(issues, [], "环境变量关闭时应零命中")
        finally:
            os.environ.pop("PG_PROPOSE_V110_HARDCODED", None)


if __name__ == "__main__":
    unittest.main()