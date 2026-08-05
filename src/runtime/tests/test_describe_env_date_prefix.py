"""Tests for pg-invoke-hook.py describe_env 日期前缀剥离 (v1.1 修复).

session 格式 <iso-date>-<change-id> (如 2026-08-04-foo), describe_env 的
产物路径与日志目录必须剥离日期前缀, 落到 .pg/changes/<change-id>/ 下,
与 pg-propose 产物目录对齐 (SKILL.md 阶段 1.6 约定产物路径不带日期).
"""

import importlib.util
import tempfile
import types
import unittest
from pathlib import Path

INVOKE_HOOK_PY = Path(__file__).resolve().parent.parent / "bin" / "pg-invoke-hook.py"


def load_invoke_hook() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("pg_invoke_hook", str(INVOKE_HOOK_PY))
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load %s" % INVOKE_HOOK_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DescribeEnvDatePrefixStripTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_invoke_hook()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project_root = Path(self.tmp.name)

    def _spec(self, session, caller="pg-propose"):
        act_cfg = {"script": ".pg/hooks/env-dev-local-describe.sh", "timeout_seconds": 60}
        return self.mod.build_describe_env_spec(
            session=session,
            env="dev-local",
            stage="dev",
            act_cfg=act_cfg,
            project_root=self.project_root,
            caller=caller,
        )

    def test_output_path_strips_date_prefix(self):
        spec = self._spec("2026-08-04-my-change")
        self.assertTrue(
            spec["output_path"].endswith(
                ".pg/changes/my-change/env-description.yaml"
            ),
            f"output_path 应剥离日期前缀: {spec['output_path']}",
        )
        self.assertNotIn("2026-08-04-my-change", spec["output_path"])

    def test_output_path_without_date_prefix_unchanged(self):
        spec = self._spec("my-change")
        self.assertTrue(
            spec["output_path"].endswith(
                ".pg/changes/my-change/env-description.yaml"
            ),
            f"无日期前缀的 session 应保持原样: {spec['output_path']}",
        )

    def test_change_id_field_strips_date_prefix(self):
        spec = self._spec("2026-08-04-my-change")
        self.assertEqual(spec["change_id"], "my-change")

    def test_session_field_keeps_original(self):
        # session 字段用于日志路由键, 保留原值
        spec = self._spec("2026-08-04-my-change")
        self.assertEqual(spec["session"], "2026-08-04-my-change")

    def test_log_dir_strips_date_prefix_for_propose(self):
        spec = self._spec("2026-08-04-my-change")
        self.assertIn(
            ".pg/changes/my-change/2-propose/dev-local-logs",
            spec["hook_log_dir"],
            f"日志目录应剥离日期前缀: {spec['hook_log_dir']}",
        )

    def test_non_date_dash_session_not_stripped(self):
        # 形如 foo-bar-baz 但无日期前缀的 session 不应被误剥离
        spec = self._spec("foo-bar-baz")
        self.assertTrue(spec["output_path"].endswith("foo-bar-baz/env-description.yaml"))

    def test_log_dir_no_date_prefix_for_plain_session(self):
        log_dir = self.mod.pg_log_dir_for_skill(
            "pg-propose", "plain-change", "dev-local", self.project_root
        )
        self.assertTrue(str(log_dir).endswith(".pg/changes/plain-change/2-propose/dev-local-logs"))


if __name__ == "__main__":
    unittest.main()
