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
            "--outputs", "/tmp/Test.java",
            "--tasks-updated", "1.1",  # v2.3: test 必填
        ])
        self.assertEqual(ok, 0, f"stderr: {err}")
        result = json.loads(out)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["summary"], "test summary")

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
            "--outputs", "/tmp/Test.java",
            "--tasks-updated", "1.1",  # v2.3: test 必填
        ])
        self.assertEqual(ok, 0, f"stderr: {err}")
        # 输出应是: completed "test summary"
        self.assertIn("completed", out)
        self.assertIn("test summary", out)

    def test_runner_mode_with_outputs_and_issues(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Fix report")
            tmp = f.name
        try:
            ok, out, err = _run([
                "--mode", "runner",
                "--status", "failed",
                "--summary", "fix failed",
                "--track", "dev.backend", "--phase", "fix",
                "--report", tmp,
                "--outputs", "/tmp/a.java,/tmp/b.java",
                "--issues", "issue1,issue2",
                "--tasks-updated", "V-backend-6",  # v2.3: fix 必填
            ])
            self.assertEqual(ok, 0, f"stderr: {err}")
            self.assertIn("failed", out)
            self.assertIn("/tmp/a.java", out)
        finally:
            os.unlink(tmp)

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

    # ============================================================
    # v2.3 新增：tasks_updated 透传
    # ============================================================

    def test_agent_mode_passes_tasks_updated(self):
        """[v2.3] agent 模式应把 --tasks-updated 透传到 JSON。"""
        ok, out, err = _run([
            "--mode", "agent",
            "--status", "completed",
            "--summary", "x",
            "--track", "dev.backend", "--phase", "test",
            "--outputs", "/tmp/Test.java",
            "--tasks-updated", "10.1",
            "--tasks-updated", "10.2",
        ])
        self.assertEqual(ok, 0, f"stderr: {err}")
        result = json.loads(out)
        self.assertEqual(result["tasks_updated"], ["10.1", "10.2"])

    def test_agent_mode_empty_tasks_updated_passes_for_simple(self):
        """[v2.3] simple 阶段空 tasks_updated 仍可生成。"""
        ok, out, err = _run([
            "--mode", "agent",
            "--status", "completed",
            "--summary", "x",
            "--track", "dev.backend", "--phase", "simple",
        ])
        self.assertEqual(ok, 0, f"stderr: {err}")
        result = json.loads(out)
        self.assertEqual(result["tasks_updated"], [])

    def test_agent_mode_test_phase_rejects_empty_tasks_updated(self):
        """[v2.3] test 阶段空 tasks_updated 应被拒。"""
        ok, out, err = _run([
            "--mode", "agent",
            "--status", "completed",
            "--summary", "x",
            "--track", "dev.backend", "--phase", "test",
            "--outputs", "/tmp/Test.java",
        ])
        self.assertEqual(ok, 1, f"stderr: {err}")
        self.assertIn("tasks-updated", err)

    # ============================================================
    # v2.4 新增：--output-path 强制落盘
    # ============================================================

    def test_output_path_writes_json(self):
        """[v2.4] --output-path 应写入合法 JSON 文件。"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".result.json", delete=False
        ) as f:
            tmp = f.name
        try:
            ok, out, err = _run([
                "--mode", "agent",
                "--status", "completed",
                "--summary", "test v2.4 output",
                "--track", "dev.backend", "--phase", "test",
                "--outputs", "/tmp/Test.java",
                "--tasks-updated", "1.1",
                "--output-path", tmp,
            ])
            self.assertEqual(ok, 0, f"stderr: {err}")
            self.assertTrue(os.path.isfile(tmp), f"文件未落盘: {tmp}")
            with open(tmp, encoding="utf-8") as f:
                content = json.load(f)
            self.assertEqual(content["status"], "completed")
            self.assertEqual(content["summary"], "test v2.4 output")
            self.assertEqual(content["outputs"], ["/tmp/Test.java"])
            self.assertEqual(content["tasks_updated"], ["1.1"])
            # ORCHESTRATOR_READY 标记应在 stderr（不污染 stdout JSON）
            self.assertIn("ORCHESTRATOR_READY", err)
            # stdout 应是合法 JSON
            json.loads(out)
        finally:
            os.unlink(tmp)

    def test_output_path_require_output_fails_on_invalid_dir(self):
        """[v2.4] --require-output + 不可写路径 → exit 2。"""
        # /nonexistent/dir/file.json 不可写（中间目录不存在）
        bad_path = "/nonexistent_xxxxx/dir/file.json"
        ok, out, err = _run([
            "--mode", "agent",
            "--status", "completed",
            "--summary", "test",
            "--track", "dev.backend", "--phase", "test",
            "--outputs", "/tmp/Test.java",
            "--tasks-updated", "1.1",
            "--output-path", bad_path,
            "--require-output",
        ])
        self.assertEqual(ok, 2, f"stderr: {err}")
        self.assertIn("写入失败", err)

    def test_output_path_optional_no_file_written(self):
        """[v2.4] 不传 --output-path 时行为同 v2.3（仅 stdout）。"""
        ok, out, err = _run([
            "--mode", "agent",
            "--status", "completed",
            "--summary", "no output path",
            "--track", "dev.backend", "--phase", "test",
            "--outputs", "/tmp/Test.java",
            "--tasks-updated", "1.1",
        ])
        self.assertEqual(ok, 0, f"stderr: {err}")
        # 输出到 stdout
        result = json.loads(out)
        self.assertEqual(result["summary"], "no output path")


if __name__ == "__main__":
    unittest.main()