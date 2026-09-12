"""Tests for pg-invoke-hook.py describe_env 路由 (v8: pg-agent / ad-hoc).

describe_env 的产物路径与日志目录按 caller 路由:
  pg-agent -> .pg/agent/<session>/env-description.yaml + .pg/agent/<session>/<env>-logs
  ad-hoc   -> .pg/ad-hoc/<session>/env-description.yaml + .pg/ad-hoc/<session>/<env>-logs
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


class DescribeEnvRoutingTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_invoke_hook()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project_root = Path(self.tmp.name)

    def _spec(self, session, caller="pg-agent"):
        act_cfg = {"script": ".pg/hooks/env-dev-local-describe.sh", "timeout_seconds": 60}
        return self.mod.build_describe_env_spec(
            session=session,
            env="dev-local",
            stage="dev",
            act_cfg=act_cfg,
            project_root=self.project_root,
            caller=caller,
        )

    def test_pg_agent_output_path(self):
        spec = self._spec("2026-09-12-my-task")
        self.assertTrue(
            spec["output_path"].endswith(
                ".pg/agent/2026-09-12-my-task/env-description.yaml"
            ),
            f"output_path 应路由到 .pg/agent/: {spec['output_path']}",
        )

    def test_pg_agent_change_id_equals_session(self):
        spec = self._spec("2026-09-12-my-task")
        self.assertEqual(spec["change_id"], "2026-09-12-my-task")

    def test_pg_agent_log_dir(self):
        spec = self._spec("2026-09-12-my-task")
        self.assertTrue(
            spec["hook_log_dir"].endswith(
                ".pg/agent/2026-09-12-my-task/dev-local-logs"
            ),
            f"日志目录应路由到 .pg/agent/: {spec['hook_log_dir']}",
        )

    def test_ad_hoc_output_path(self):
        spec = self._spec("my-session", caller="ad-hoc")
        self.assertTrue(
            spec["output_path"].endswith(
                ".pg/ad-hoc/my-session/env-description.yaml"
            ),
            f"output_path 应路由到 .pg/ad-hoc/: {spec['output_path']}",
        )

    def test_log_dir_ad_hoc(self):
        log_dir = self.mod.pg_log_dir_for_skill(
            "ad-hoc", "my-session", "dev-local", self.project_root
        )
        self.assertTrue(str(log_dir).endswith(".pg/ad-hoc/my-session/dev-local-logs"))

    def test_log_dir_pg_agent(self):
        log_dir = self.mod.pg_log_dir_for_skill(
            "pg-agent", "2026-09-12-my-task", "dev-local", self.project_root
        )
        self.assertTrue(str(log_dir).endswith(".pg/agent/2026-09-12-my-task/dev-local-logs"))


if __name__ == "__main__":
    unittest.main()
