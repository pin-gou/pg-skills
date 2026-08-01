"""Tests for pg-invoke-hook.py restart_all_instances action.

v3.x: restart_all_instances 是 env-level action, 内部展开为
stop (逆序) → start (正序) → health_check (仅当声明) 三阶段.
通过 subprocess.run 调用 pg-run-hook.py 多次, 任一失败早退.

测试策略: 不直接 import pg_invoke_hook (避免依赖路径), 用 subprocess.run 调用
pg-invoke-hook.py CLI, mock pg-run-hook.py 行为, 解析 captured stdout / 命令参数.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


_PG_INVOKE_HOOK = Path(__file__).resolve().parent.parent / "bin" / "pg-invoke-hook.py"


def _make_project_root(tmp: Path) -> Path:
    """构造最小 project.yaml 含 2 role × 1 instance."""
    pg_dir = tmp / ".pg"
    pg_dir.mkdir(exist_ok=True)
    (pg_dir / "project.yaml").write_text(
        "environments:\n"
        "  test-env:\n"
        "    roles:\n"
        "      backend:\n"
        "        instances:\n"
        "          - {name: backend-1, host: localhost}\n"
        "        actions:\n"
        "          start:\n"
        "            script: /tmp/fake-start.sh\n"
        "          stop:\n"
        "            script: /tmp/fake-stop.sh\n"
        "          health_check:\n"
        "            script: /tmp/fake-health.sh\n"
        "      frontend:\n"
        "        instances:\n"
        "          - {name: frontend-1, host: localhost}\n"
        "        actions:\n"
        "          start:\n"
            "            script: /tmp/fake-start.sh\n"
        "          stop:\n"
        "            script: /tmp/fake-stop.sh\n"
    )
    return tmp


def _make_fake_pg_run_hook(tmp: Path) -> Path:
    """构造假的 pg-run-hook.py, 把收到的 spec 写到文件 + 返回预置 exit code."""
    fake = tmp / "pg-run-hook.py"
    rc_file = tmp / "next_rc.txt"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys, os\n"
        "spec = json.load(sys.stdin)\n"
        "out_path = os.path.join(os.path.dirname(__file__), 'specs.jsonl')\n"
        "with open(out_path, 'a') as f:\n"
        "    f.write(json.dumps(spec) + '\\n')\n"
        "rc_file = os.path.join(os.path.dirname(__file__), 'next_rc.txt')\n"
        "if os.path.exists(rc_file):\n"
        "    with open(rc_file) as f:\n"
        "        rcs = f.read().strip().split('\\n')\n"
        "    if rcs:\n"
        "        sys.exit(int(rcs.pop(0)))\n"
        "sys.exit(0)\n"
    )
    fake.chmod(0o755)
    return fake


class TestRestartAllInstances(unittest.TestCase):
    """通过 mock subprocess.run 验证 restart_all_instances 的三阶段行为."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.project_root = _make_project_root(self.tmp)
        # 构造 .pg/skills/src/runtime/lib/pg-run-hook.py (find_pg_skills_root + lib/)
        skills_lib = self.project_root / ".pg" / "skills" / "src" / "runtime" / "lib"
        skills_lib.mkdir(parents=True)
        self.fake_runner = _make_fake_pg_run_hook(skills_lib)

    def _read_captured_specs(self) -> list:
        specs_file = self.tmp / ".pg" / "skills" / "src" / "runtime" / "lib" / "specs.jsonl"
        if not specs_file.exists():
            return []
        return [json.loads(line) for line in specs_file.read_text().strip().splitlines()]

    def test_three_phase_order_and_count(self):
        """restart_all_instances 应调用 pg-run-hook.py 5 次:
        2 stop (逆序: frontend → backend) + 2 start (正序: backend → frontend) + 1 health_check (仅 backend)."""
        # 不写 next_rc.txt, 全部返回 0
        env = os.environ.copy()
        env["PG_PROJECT_ROOT"] = str(self.project_root)
        proc = subprocess.run(
            ["python3", str(_PG_INVOKE_HOOK), "invoke-hook",
             "--session", "test",
             "--env", "test-env",
             "--action", "restart_all_instances",
             "--skill", "pg-build"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr}")
        specs = self._read_captured_specs()
        self.assertEqual(len(specs), 5, f"expected 5 specs, got {len(specs)}: stderr={proc.stderr}")
        actions = [s["hook_type"] for s in specs]
        # 阶段 1: 逆序 stop (frontend-1 → backend-1)
        self.assertEqual(actions[:2], ["stop", "stop"])
        self.assertEqual(specs[0]["instance_name"], "frontend-1")
        self.assertEqual(specs[1]["instance_name"], "backend-1")
        # 阶段 2: 正序 start (backend-1 → frontend-1)
        self.assertEqual(actions[2:4], ["start", "start"])
        self.assertEqual(specs[2]["instance_name"], "backend-1")
        self.assertEqual(specs[3]["instance_name"], "frontend-1")
        # 阶段 3: 仅 backend 声明了 health_check → 1 次
        self.assertEqual(actions[4], "health_check")
        self.assertEqual(specs[4]["instance_name"], "backend-1")
        # frontend 没声明 health_check, 不调用

    def test_stop_failure_breaks_early(self):
        """stop 阶段失败时早退, 整体 exit 非 0."""
        rc_file = self.tmp / ".pg" / "skills" / "src" / "runtime" / "lib" / "next_rc.txt"
        rc_file.write_text("1\n")  # 第 1 次 stop 返回 1
        env = os.environ.copy()
        env["PG_PROJECT_ROOT"] = str(self.project_root)
        proc = subprocess.run(
            ["python3", str(_PG_INVOKE_HOOK), "invoke-hook",
             "--session", "test",
             "--env", "test-env",
             "--action", "restart_all_instances",
             "--skill", "pg-build"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(proc.returncode, 1)
        specs = self._read_captured_specs()
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["hook_type"], "stop")

    def test_role_arg_rejected(self):
        """--action restart_all_instances 忽略 --role/--instance, 传了则报错."""
        env = os.environ.copy()
        env["PG_PROJECT_ROOT"] = str(self.project_root)
        proc = subprocess.run(
            ["python3", str(_PG_INVOKE_HOOK), "invoke-hook",
             "--session", "test",
             "--env", "test-env",
             "--action", "restart_all_instances",
             "--role", "backend",
             "--instance", "backend-1",
             "--skill", "pg-build"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("ignores --role/--instance", proc.stderr)

    def test_env_without_roles(self):
        """env 无 roles 时报错."""
        empty_root = self.tmp / "empty"
        empty_root.mkdir()
        (empty_root / ".pg").mkdir()
        (empty_root / ".pg/project.yaml").write_text(
            "environments:\n  test-env:\n"
        )
        env = os.environ.copy()
        env["PG_PROJECT_ROOT"] = str(empty_root)
        proc = subprocess.run(
            ["python3", str(_PG_INVOKE_HOOK), "invoke-hook",
             "--session", "test",
             "--env", "test-env",
             "--action", "restart_all_instances",
             "--skill", "pg-build"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no roles", proc.stderr)


    def test_aggregate_result_json_written_on_success(self):
        """v3.12: 成功时写 PG_RESULT_FILE 聚合 result.json, status=pass."""
        result_file = self.tmp / "restart-result.json"
        env = os.environ.copy()
        env["PG_PROJECT_ROOT"] = str(self.project_root)
        env["PG_RESULT_FILE"] = str(result_file)
        proc = subprocess.run(
            ["python3", str(_PG_INVOKE_HOOK), "invoke-hook",
             "--session", "test",
             "--env", "test-env",
             "--action", "restart_all_instances",
             "--skill", "pg-build"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr}")
        self.assertTrue(result_file.exists(), "PG_RESULT_FILE 应被写入")
        data = json.loads(result_file.read_text())
        self.assertEqual(data["status"], "pass")
        self.assertEqual(data["exit_code"], 0)
        self.assertEqual(data["action"], "restart_all_instances")
        # 5 个子 spec (2 stop + 2 start + 1 health_check) 的 returncode 全 0
        self.assertEqual(len(data["sub_results"]), 5)
        self.assertTrue(all(r["returncode"] == 0 for r in data["sub_results"]))

    def test_aggregate_result_json_written_on_failure(self):
        """v3.12: 失败时写 PG_RESULT_FILE, status=fail, 含已执行的子 spec."""
        rc_file = self.tmp / ".pg" / "skills" / "src" / "runtime" / "lib" / "next_rc.txt"
        rc_file.write_text("1\n")  # 第 1 次 stop 返回 1
        result_file = self.tmp / "restart-result.json"
        env = os.environ.copy()
        env["PG_PROJECT_ROOT"] = str(self.project_root)
        env["PG_RESULT_FILE"] = str(result_file)
        proc = subprocess.run(
            ["python3", str(_PG_INVOKE_HOOK), "invoke-hook",
             "--session", "test",
             "--env", "test-env",
             "--action", "restart_all_instances",
             "--skill", "pg-build"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(result_file.exists(), "失败时 PG_RESULT_FILE 仍应写入")
        data = json.loads(result_file.read_text())
        self.assertEqual(data["status"], "fail")
        self.assertEqual(data["exit_code"], 1)
        # 早退后只执行了 1 个 spec (失败的 stop)
        self.assertEqual(len(data["sub_results"]), 1)
        self.assertEqual(data["sub_results"][0]["returncode"], 1)


if __name__ == "__main__":
    unittest.main()