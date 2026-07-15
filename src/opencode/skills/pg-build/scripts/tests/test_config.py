"""Config 解析函数单元测试。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.config import (
    resolve_module_details,
    resolve_module_roots,
    resolve_test_commands,
    resolve_env_instances,
    resolve_hooks,
    resolve_build_rules,
    load_project_config,
)


class TestResolveModuleDetails(unittest.TestCase):
    def setUp(self):
        self.config = {
            "modules": {
                "backend": {
                    "root": "webvirt-backend",
                    "language": "java",
                    "build": "cd webvirt-backend && mvn clean install -DskipTests",
                    "lint": "cd webvirt-backend && mvn checkstyle:check",
                    "test": {
                        "unit": "cd webvirt-backend && mvn test",
                        "integration": {"cmd": ".pg/hooks/backend-test.sh", "timeout_seconds": 3600},
                    },
                },
                "agent-proto": {
                    "root": "webvirt-agent-proto",
                    "language": "proto",
                    "build": "cd webvirt-agent && make proto",
                },
            },
        }

    def test_backend_module(self):
        result = resolve_module_details(self.config, ["backend"])
        self.assertIn("module: backend", result)
        self.assertIn("root: webvirt-backend", result)
        self.assertIn("test.unit: cd webvirt-backend && mvn test", result)

    def test_multiple_modules(self):
        result = resolve_module_details(self.config, ["backend", "agent-proto"])
        self.assertIn("module: backend", result)
        self.assertIn("module: agent-proto", result)
        self.assertIn("root: webvirt-agent-proto", result)

    def test_empty_modules(self):
        result = resolve_module_details(self.config, [])
        self.assertEqual(result, "")

    def test_unknown_module(self):
        result = resolve_module_details(self.config, ["nonexistent"])
        self.assertIn("root:", result)


class TestResolveModuleRoots(unittest.TestCase):
    def setUp(self):
        self.config = {
            "modules": {
                "backend": {"root": "webvirt-backend"},
                "agent-proto": {"root": "webvirt-agent-proto"},
            },
        }

    def test_single(self):
        self.assertEqual(resolve_module_roots(self.config, ["backend"]),
                         "['webvirt-backend']")

    def test_multiple(self):
        roots = resolve_module_roots(self.config, ["backend", "agent-proto"])
        self.assertIn("webvirt-backend", roots)
        self.assertIn("webvirt-agent-proto", roots)

    def test_dedup_same_root(self):
        result = resolve_module_roots(self.config, ["backend", "backend"])
        self.assertEqual(result.count("webvirt-backend"), 1)


class TestResolveTestCommands(unittest.TestCase):
    def setUp(self):
        self.config = {
            "modules": {
                "backend": {"test": {"unit": "cd backend && mvn test"}},
                "frontend": {"test": {"unit": "cd frontend && pnpm test"}},
            },
        }

    def test_single_module(self):
        self.assertEqual(
            resolve_test_commands(self.config, ["backend"]),
            "cd backend && mvn test",
        )

    def test_two_modules_joined(self):
        cmd = resolve_test_commands(self.config, ["backend", "frontend"])
        self.assertIn("backend && mvn test", cmd)
        self.assertIn("frontend && pnpm test", cmd)

    def test_empty_modules(self):
        self.assertEqual(resolve_test_commands(self.config, []), "")

    def test_no_test_key(self):
        result = resolve_test_commands(self.config, ["backend"], "nonexistent")
        self.assertEqual(result, "")

    def test_dict_form(self):
        cfg = {"modules": {"m": {"test": {"unit": {"cmd": "cd m && pytest", "timeout_seconds": 60}}}}}
        self.assertEqual(resolve_test_commands(cfg, ["m"]), "cd m && pytest")


class TestResolveEnvInstances(unittest.TestCase):
    def setUp(self):
        self.config = {
            "environments": {
                "dev-local": {
                    "roles": {
                        "backend": {
                            "instances": [
                                {"name": "backend-1", "host": "localhost", "port": 9080},
                            ],
                        },
                        "frontend": {
                            "instances": [
                                {"name": "frontend-1", "host": "localhost", "port": 3008},
                            ],
                        },
                    },
                },
            },
        }

    def test_returns_yaml(self):
        result = resolve_env_instances(self.config, "dev-local")
        self.assertIn("backend", result)
        self.assertIn("backend-1", result)
        self.assertIn("localhost", result)
        self.assertIn("9080", result)

    def test_nonexistent_env(self):
        self.assertEqual(resolve_env_instances(self.config, "nonexistent"), "")

    def test_no_roles(self):
        cfg = {"environments": {"empty": {}}}
        self.assertEqual(resolve_env_instances(cfg, "empty"), "")


class TestResolveHooks(unittest.TestCase):
    def setUp(self):
        self.config = {
            "environments": {
                "dev-local": {
                    "roles": {
                        "backend": {
                            "actions": {
                                "start": {
                                    "host": "localhost",
                                    "script": ".pg/hooks/role-backend-start.sh",
                                    "timeout_seconds": 300,
                                    "description": "Start backend",
                                },
                                "stop": {
                                    "host": "localhost",
                                    "script": ".pg/hooks/role-backend-stop.sh",
                                    "timeout_seconds": 30,
                                },
                            },
                        },
                    },
                },
            },
        }

    def test_returns_yaml(self):
        result = resolve_hooks(self.config, "dev-local")
        self.assertIn("backend", result)
        self.assertIn("role-backend-start.sh", result)

    def test_nonexistent_env(self):
        self.assertEqual(resolve_hooks(self.config, "nonexistent"), "")

    def test_no_actions(self):
        cfg = {"environments": {"e": {"roles": {"r": {}}}}}
        self.assertEqual(resolve_hooks(cfg, "e"), "")


class TestResolveEnvInstancesOrder(unittest.TestCase):
    """v3.7: role 顺序必须保留 environments.<env>.roles 的源码书写顺序。

    必须与 `.pg/skills/src/runtime/bin/pg-run` 的 `_run_env_start_all()`
    遍历顺序一致（for role_name, role_cfg in roles.items()）。
    PyYAML 默认 sort_keys=True 会按字母序输出 dict key，导致 dispatch 与
    pg-run 看到相反顺序——回归测试必须钉死此不变量。
    """

    def setUp(self):
        self.config = {
            "environments": {
                "dev-local": {
                    "roles": {
                        # 关键：源码顺序是 backend → frontend → agent，
                        # 字母序会输出 agent, backend, frontend。这是回归
                        # 测试要捕获的不变量破缺。
                        "backend": {
                            "instances": [
                                {"name": "backend-1", "host": "localhost", "port": 9080},
                            ],
                        },
                        "frontend": {
                            "instances": [
                                {"name": "frontend-1", "host": "localhost", "port": 3008},
                            ],
                        },
                        "agent": {
                            "instances": [
                                {"name": "agent-1", "host": "localhost"},
                            ],
                        },
                    },
                },
            },
        }
        self.expected_order = ["backend", "frontend", "agent"]

    def _role_keys(self, yaml_text: str) -> list[str]:
        """从 yaml.dump 输出里抓顶层 role key（首列 'word:' 形式）。"""
        import yaml as _yaml
        parsed = _yaml.safe_load(yaml_text)
        return list(parsed.keys())

    def test_preserves_source_order_not_alphabetical(self):
        result = resolve_env_instances(self.config, "dev-local")
        keys = self._role_keys(result)
        self.assertEqual(
            keys, self.expected_order,
            f"role 顺序应保留源码顺序 {self.expected_order}，"
            f"实际拿到 {keys}。PyYAML 默认 sort_keys=True 会导致字母序"
            f"输出（agent, backend, frontend），必须显式 sort_keys=False"
            f"才能与 pg-run._run_env_start_all() 的遍历顺序对齐。"
        )

    def test_matches_roles_items_traversal_order(self):
        """与 pg-run._run_env_start_all() 中 for r in roles.items() 顺序一致。"""
        result = resolve_env_instances(self.config, "dev-local")
        # pg-run 等价遍历
        pgrun_order = list(self.config["environments"]["dev-local"]["roles"].keys())
        import yaml as _yaml
        dispatched_order = list(_yaml.safe_load(result).keys())
        self.assertEqual(
            dispatched_order, pgrun_order,
            "dispatch 渲染顺序必须与 pg-run._run_env_start_all 遍历顺序一致"
        )


class TestResolveHooksOrder(unittest.TestCase):
    """v3.7: 同样为 resolve_hooks 钉死源码顺序。"""

    def setUp(self):
        self.config = {
            "environments": {
                "dev-local": {
                    "roles": {
                        "backend": {
                            "actions": {
                                "start": {
                                    "host": "localhost",
                                    "script": ".pg/hooks/role-backend-start.sh",
                                    "timeout_seconds": 300,
                                },
                            },
                        },
                        "frontend": {
                            "actions": {
                                "start": {
                                    "host": "localhost",
                                    "script": ".pg/hooks/role-frontend-start.sh",
                                    "timeout_seconds": 120,
                                },
                            },
                        },
                        "agent": {
                            "actions": {
                                "start": {
                                    "host": "localhost",
                                    "script": ".pg/hooks/role-agent-start.sh",
                                    "timeout_seconds": 120,
                                },
                            },
                        },
                    },
                },
            },
        }

    def test_preserves_source_order_not_alphabetical(self):
        result = resolve_hooks(self.config, "dev-local")
        import yaml as _yaml
        keys = list(_yaml.safe_load(result).keys())
        self.assertEqual(
            keys, ["backend", "frontend", "agent"],
            f"hooks 顺序应保留源码顺序，实际拿到 {keys}。"
            f"PyYAML 默认 sort_keys=True 会按字母序输出 dict key。"
        )


class TestResolveBuildRules(unittest.TestCase):
    def test_matches_target(self):
        config = {
            "build_rules": [
                {
                    "id": "checklist",
                    "type": "inject-prompt",
                    "target_agent": "pg-build/dev",
                    "position": "prepend",
                    "template": "[CHECKLIST]\n- item 1",
                },
                {
                    "id": "verify_step",
                    "type": "inject-prompt",
                    "target_agent": "pg-build/verify",
                    "position": "prepend",
                    "template": "[VERIFY]\n- step 1",
                },
                {
                    "id": "other_type",
                    "type": "other",
                    "target_agent": "pg-build/dev",
                    "template": "should be ignored",
                },
            ],
        }
        prepend, append = resolve_build_rules(config, "pg-build/dev")
        self.assertIn("[CHECKLIST]", prepend)
        self.assertEqual(append, "")

    def test_no_match(self):
        prepend, append = resolve_build_rules({}, "pg-build/dev")
        self.assertEqual(prepend, "")
        self.assertEqual(append, "")

    def test_append_default(self):
        config = {
            "build_rules": [
                {
                    "id": "r1",
                    "type": "inject-prompt",
                    "target_agent": "pg-build/verify",
                    "template": "[APPEND]\n- item",
                },
            ],
        }
        prepend, append = resolve_build_rules(config, "pg-build/verify")
        self.assertEqual(prepend, "")
        self.assertIn("[APPEND]", append)


class TestLoadProjectConfig(unittest.TestCase):
    def test_nonexistent_file(self):
        result = load_project_config("/nonexistent")
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()