"""pg-build-result 脚本测试 (v2.1 新增)。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

_SCRIPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "pg-build-result",
)


def _run(args: list[str]) -> tuple[int, str, str]:
    """执行 pg-build-result，返回 (exit_code, stdout, stderr)。"""
    proc = subprocess.run(
        [sys.executable, _SCRIPT_PATH] + args,
        capture_output=True, text=True, timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


class TestPgBuildResult(unittest.TestCase):
    """双模式 CLI 测试。"""

    # ---- agent 模式 ----

    def test_agent_mode_valid_emits_json(self):
        ok, out, err = _run([
            "--mode", "agent",
            "--status", "completed",
            "--summary", "test summary",
            "--track", "dev.backend", "--phase", "test",
        ])
        self.assertEqual(ok, 0, f"stderr: {err}")
        result = json.loads(out)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["summary"], "test summary")
        self.assertEqual(result["track"] if "track" in result else result["outputs"], result["outputs"])

    def test_agent_mode_with_evidence_and_report(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False
        ) as f:
            f.write("# PASS\nverify ok")
            tmp = f.name
        try:
            ok, out, err = _run([
                "--mode", "agent",
                "--status", "pass",
                "--summary", "8/9 检查通过, gate_score: 91, p0_failures: []",
                "--track", "dev.backend", "--phase", "gate",
                "--report", tmp,
                "--evidence", tmp,
            ])
            self.assertEqual(ok, 0, f"stderr: {err}")
            result = json.loads(out)
            self.assertEqual(result["status"], "pass")
            self.assertIn(tmp, result["evidence_paths"])
            self.assertEqual(result["report_path"], tmp)
        finally:
            os.unlink(tmp)

    # ---- runner 模式 ----

    def test_runner_mode_emits_cli_string(self):
        ok, out, err = _run([
            "--mode", "runner",
            "--status", "completed",
            "--summary", "test summary",
            "--track", "dev.backend", "--phase", "test",
        ])
        self.assertEqual(ok, 0, f"stderr: {err}")
        # 输出应是: completed "test summary"
        self.assertIn("completed", out)
        self.assertIn("test summary", out)

    def test_runner_mode_with_outputs_and_issues(self):
        ok, out, err = _run([
            "--mode", "runner",
            "--status", "failed",
            "--summary", "fix failed",
            "--track", "dev.backend", "--phase", "fix",
            "--outputs", "/tmp/a.java,/tmp/b.java",
            "--issues", "issue1,issue2",
        ])
        self.assertEqual(ok, 0, f"stderr: {err}")
        self.assertIn("failed", out)
        self.assertIn("/tmp/a.java", out)

    # ---- 校验失败场景 ----

    def test_invalid_status_rejected(self):
        ok, out, err = _run([
            "--mode", "agent",
            "--status", "BOGUS",
            "--summary", "x",
            "--track", "dev.backend", "--phase", "test",
        ])
        self.assertEqual(ok, 1)
        self.assertIn("schema_violation", err)

    def test_phase_status_mismatch_rejected(self):
        """[v2.1] gate 阶段不接受 completed。"""
        # 创建临时 report 文件，让校验走到 phase-status 检查
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# PASS")
            tmp = f.name
        try:
            ok, out, err = _run([
                "--mode", "agent",
                "--status", "completed",
                "--summary", "x gate_score: 90, p0_failures: []",
                "--track", "dev.backend", "--phase", "gate",
                "--report", tmp,
                "--evidence", tmp,
            ])
            self.assertEqual(ok, 1)
            self.assertIn("gate", err)
        finally:
            os.unlink(tmp)

    def test_gate_missing_report_rejected(self):
        ok, out, err = _run([
            "--mode", "agent",
            "--status", "pass",
            "--summary", "x gate_score: 90, p0_failures: []",
            "--track", "dev.backend", "--phase", "gate",
            "--evidence", "/tmp/some_ev.md",
        ])
        self.assertEqual(ok, 1)
        self.assertIn("report_path", err)

    def test_gate_missing_score_rejected(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False
        ) as f:
            f.write("# PASS")
            tmp = f.name
        try:
            ok, out, err = _run([
                "--mode", "agent",
                "--status", "pass",
                "--summary", "no score here",
                "--track", "dev.backend", "--phase", "gate",
                "--report", tmp,
                "--evidence", tmp,
            ])
            self.assertEqual(ok, 1)
            self.assertIn("gate_score", err)
        finally:
            os.unlink(tmp)

    def test_summary_too_long_rejected(self):
        long = "x" * 201
        ok, out, err = _run([
            "--mode", "agent",
            "--status", "completed",
            "--summary", long,
            "--track", "dev.backend", "--phase", "test",
        ])
        self.assertEqual(ok, 1)
        self.assertIn("200", err)

    def test_help_flag(self):
        """--help 应正常退出，不算 bug。"""
        proc = subprocess.run(
            [sys.executable, _SCRIPT_PATH, "--help"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("usage", proc.stdout.lower())


if __name__ == "__main__":
    unittest.main()