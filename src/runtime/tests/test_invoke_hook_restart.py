"""Tests for pg-invoke-hook.py per-role restart action fallback.

当 --action restart 且 role.actions.restart 未定义时, 自动 fallback 为
stop → start → [health_check] 三阶段 (同 restart_all_instances 模式但针对单个 instance).

当 role.actions.restart 已显式定义时, 走正常单 spec 路径.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


_PG_INVOKE_HOOK = Path(__file__).resolve().parent.parent / "bin" / "pg-invoke-hook.py"


def _make_project_root(tmp: Path, with_health_check: bool = True,
                       with_restart: bool = False) -> Path:
    """构造最小 project.yaml 含 1 role × 1 instance.

    roles 是 array of dict (与 project.yaml v3.7+ 设计一致).
    """
    pg_dir = tmp / ".pg"
    pg_dir.mkdir(exist_ok=True)
    lines = [
        "environments:\n",
        "  test-env:\n",
        "    roles:\n",
        "      - name: backend\n",
        "        instances:\n",
        "          - {name: backend-1, host: localhost}\n",
        "        actions:\n",
        "          start:\n",
        "            script: /tmp/fake-start.sh\n",
        "            wait_for_completion: true\n",
        "          stop:\n",
        "            script: /tmp/fake-stop.sh\n",
    ]
    if with_health_check:
        lines.append("          health_check:\n")
        lines.append("            script: /tmp/fake-health.sh\n")
    if with_restart:
        lines.append("          restart:\n")
        lines.append("            script: /tmp/fake-restart.sh\n")
    (pg_dir / "project.yaml").write_text("".join(lines))
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
        "        remaining = [r for r in rcs[1:] if r != '']\n"
        "        with open(rc_file, 'w') as f:\n"
        "            f.write('\\n'.join(remaining))\n"
        "        sys.exit(int(rcs[0]))\n"
        "sys.exit(0)\n"
    )
    fake.chmod(0o755)
    return fake


class TestRestartFallback(unittest.TestCase):
    """验证 per-role restart 未定义时的 fallback 行为."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.project_root = _make_project_root(self.tmp, with_health_check=True)
        skills_lib = self.project_root / ".pg" / "skills" / "src" / "runtime" / "lib"
        skills_lib.mkdir(parents=True, exist_ok=True)
        self.fake_runner = _make_fake_pg_run_hook(skills_lib)

    def _read_captured_specs(self) -> list:
        specs_file = self.tmp / ".pg" / "skills" / "src" / "runtime" / "lib" / "specs.jsonl"
        if not specs_file.exists():
            return []
        return [json.loads(line) for line in specs_file.read_text().strip().splitlines()]

    def test_restart_fallback_stop_start_health_check(self):
        """restart 未定义时 fallback: stop → start → health_check (3 个 spec)."""
        env = os.environ.copy()
        env["PG_PROJECT_ROOT"] = str(self.project_root)
        proc = subprocess.run(
            ["python3", str(_PG_INVOKE_HOOK), "invoke-hook",
             "--session", "test",
             "--env", "test-env",
             "--role", "backend",
             "--instance", "backend-1",
             "--action", "restart",
             "--skill", "pg-build"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr}")
        specs = self._read_captured_specs()
        self.assertEqual(len(specs), 3, f"expected 3 specs, got {len(specs)}: stderr={proc.stderr}")
        actions = [s["hook_type"] for s in specs]
        self.assertEqual(actions, ["stop", "start", "health_check"])
        for s in specs:
            self.assertEqual(s["instance_name"], "backend-1")
        self.assertTrue(specs[0]["wait_for_completion"])
        self.assertTrue(specs[1]["wait_for_completion"])

    def test_restart_fallback_stop_start_only(self):
        """health_check 未声明时, fallback 只产生 stop + start 两个 spec."""
        project_root = _make_project_root(self.tmp, with_health_check=False)
        skills_lib = project_root / ".pg" / "skills" / "src" / "runtime" / "lib"
        skills_lib.mkdir(parents=True, exist_ok=True)
        _make_fake_pg_run_hook(skills_lib)
        env = os.environ.copy()
        env["PG_PROJECT_ROOT"] = str(project_root)
        proc = subprocess.run(
            ["python3", str(_PG_INVOKE_HOOK), "invoke-hook",
             "--session", "test",
             "--env", "test-env",
             "--role", "backend",
             "--instance", "backend-1",
             "--action", "restart",
             "--skill", "pg-build"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr}")
        specs = self._read_captured_specs()
        self.assertEqual(len(specs), 2, f"expected 2 specs, got {len(specs)}: stderr={proc.stderr}")
        actions = [s["hook_type"] for s in specs]
        self.assertEqual(actions, ["stop", "start"])

    def test_stop_failure_breaks_early(self):
        """stop 阶段失败时早退, 不执行 start 或 health_check."""
        rc_file = self.tmp / ".pg" / "skills" / "src" / "runtime" / "lib" / "next_rc.txt"
        rc_file.write_text("1\n")
        env = os.environ.copy()
        env["PG_PROJECT_ROOT"] = str(self.project_root)
        proc = subprocess.run(
            ["python3", str(_PG_INVOKE_HOOK), "invoke-hook",
             "--session", "test",
             "--env", "test-env",
             "--role", "backend",
             "--instance", "backend-1",
             "--action", "restart",
             "--skill", "pg-build"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(proc.returncode, 1)
        specs = self._read_captured_specs()
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["hook_type"], "stop")

    def test_start_failure_breaks_early(self):
        """start 阶段失败时早退, 不执行 health_check."""
        rc_file = self.tmp / ".pg" / "skills" / "src" / "runtime" / "lib" / "next_rc.txt"
        rc_file.write_text("0\n1\n")
        env = os.environ.copy()
        env["PG_PROJECT_ROOT"] = str(self.project_root)
        proc = subprocess.run(
            ["python3", str(_PG_INVOKE_HOOK), "invoke-hook",
             "--session", "test",
             "--env", "test-env",
             "--role", "backend",
             "--instance", "backend-1",
             "--action", "restart",
             "--skill", "pg-build"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(proc.returncode, 1)
        specs = self._read_captured_specs()
        self.assertEqual(len(specs), 2)
        self.assertEqual(specs[0]["hook_type"], "stop")
        self.assertEqual(specs[1]["hook_type"], "start")

    def test_restart_not_defined_no_stop(self):
        """restart 未定义, 且 stop 也未定义 → 报错."""
        pg_dir = self.tmp / ".pg"
        (pg_dir / "project.yaml").write_text(
            "environments:\n"
            "  test-env:\n"
            "    roles:\n"
            "      - name: backend\n"
            "        instances:\n"
            "          - {name: backend-1, host: localhost}\n"
            "        actions:\n"
            "          start:\n"
            "            script: /tmp/fake-start.sh\n"
        )
        env = os.environ.copy()
        env["PG_PROJECT_ROOT"] = str(self.project_root)
        proc = subprocess.run(
            ["python3", str(_PG_INVOKE_HOOK), "invoke-hook",
             "--session", "test",
             "--env", "test-env",
             "--role", "backend",
             "--instance", "backend-1",
             "--action", "restart",
             "--skill", "pg-build"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("fallback 'stop' is also not defined", proc.stderr)

    def test_restart_not_defined_no_start(self):
        """restart 未定义, 且 start 也未定义 → 报错."""
        pg_dir = self.tmp / ".pg"
        (pg_dir / "project.yaml").write_text(
            "environments:\n"
            "  test-env:\n"
            "    roles:\n"
            "      - name: backend\n"
            "        instances:\n"
            "          - {name: backend-1, host: localhost}\n"
            "        actions:\n"
            "          stop:\n"
            "            script: /tmp/fake-stop.sh\n"
        )
        env = os.environ.copy()
        env["PG_PROJECT_ROOT"] = str(self.project_root)
        proc = subprocess.run(
            ["python3", str(_PG_INVOKE_HOOK), "invoke-hook",
             "--session", "test",
             "--env", "test-env",
             "--role", "backend",
             "--instance", "backend-1",
             "--action", "restart",
             "--skill", "pg-build"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("fallback 'start' is also not defined", proc.stderr)


class TestRestartExplicit(unittest.TestCase):
    """验证 per-role restart 已显式定义时走正常单 spec 路径."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.project_root = _make_project_root(self.tmp, with_health_check=False,
                                                with_restart=True)
        skills_lib = self.project_root / ".pg" / "skills" / "src" / "runtime" / "lib"
        skills_lib.mkdir(parents=True, exist_ok=True)
        self.fake_runner = _make_fake_pg_run_hook(skills_lib)

    def _read_captured_specs(self) -> list:
        specs_file = self.tmp / ".pg" / "skills" / "src" / "runtime" / "lib" / "specs.jsonl"
        if not specs_file.exists():
            return []
        return [json.loads(line) for line in specs_file.read_text().strip().splitlines()]

    def test_restart_explicitly_defined_single_spec(self):
        """restart 显式定义时, 生成单条 spec, hook_type=restart."""
        env = os.environ.copy()
        env["PG_PROJECT_ROOT"] = str(self.project_root)
        proc = subprocess.run(
            ["python3", str(_PG_INVOKE_HOOK), "invoke-hook",
             "--session", "test",
             "--env", "test-env",
             "--role", "backend",
             "--instance", "backend-1",
             "--action", "restart",
             "--skill", "pg-build"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr}")
        specs = self._read_captured_specs()
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["hook_type"], "restart")
        self.assertEqual(specs[0]["instance_name"], "backend-1")


if __name__ == "__main__":
    unittest.main()