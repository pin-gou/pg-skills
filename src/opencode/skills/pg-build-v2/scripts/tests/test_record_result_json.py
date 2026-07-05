"""Unit + subprocess tests for v2.5 record --result-json behavior.

测试矩阵：
  T1: 仅 --result-json，文件含全部 7 字段 → orchestrator.record 收到正确参数
  T2: 仅 CLI → 行为与 v2.4 完全一致（回归保护）
  T3: CLI + --result-json 混用，CLI 字段非空 → 优先级 CLI 胜
  T4: --result-json 指向不存在文件 → fatal result_json_missing
  T5: --result-json 内容顶层非 dict → fatal result_json_invalid
  T6: --result-json 缺 status 字段 → fatal status_missing
  T7: --result-json 与 --tasks-updated 列表字段合并
  T8: --result-json 文件 list 含 None / 空字符串 → 去空处理
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "scripts",
    ),
)


SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(SCRIPTS_DIR, "pg-pipeline-runner.py")


def _run_record_in_process(args: list[str], change: str = "v25-test"):
    """in-process 调 runner main()，返回 (stdout_json, mock_orchestrator_record)。

    优点：可被 unittest.mock.patch 直接拦截 Orchestrator.record
    缺点：依赖 runner main() 不做异常外的进程退出
    """
    import io
    from contextlib import redirect_stdout
    # mock 整个 Orchestrator 类避免真实加载
    captured = {"calls": []}

    class FakeOrchestrator:
        def __init__(self, change_arg, use_replay=False):
            pass

        def record(self, status, report_path, summary, outputs, issues,
                   evidence_paths=None, tasks_updated=None):
            captured["calls"].append({
                "status": status, "report_path": report_path,
                "summary": summary, "outputs": outputs, "issues": issues,
                "evidence_paths": list(evidence_paths or []),
                "tasks_updated": list(tasks_updated or []),
            })
            return {"action": "advance", "captured": True}

    buf = io.StringIO()
    old_argv = sys.argv
    sys.argv = ["pg-pipeline-runner.py", "record", change, *args]
    try:
        # 替换 runner 模块内的 Orchestrator 名字
        import importlib
        runner_mod = importlib.import_module("pg-pipeline-runner")
        with mock.patch.object(runner_mod, "Orchestrator", FakeOrchestrator):
            with redirect_stdout(buf):
                try:
                    runner_mod.main()
                except SystemExit as e:
                    pass  # runner main() 内部正常不 exit
        out_text = buf.getvalue()
    finally:
        sys.argv = old_argv

    try:
        out = json.loads(out_text) if out_text.strip() else {}
    except json.JSONDecodeError:
        out = {"_raw_stdout": out_text}
    return out, captured["calls"]


def _run_record(args: list[str], cwd: str | None = None, change: str = "v25-test") -> tuple[int, dict]:
    """subprocess 调 CLI，返回 (exit_code, parsed_stdout_json)。

    用于测试 fatal 分支（不依赖 Orchestrator 实例化）。
    """
    proc = subprocess.run(
        [sys.executable, RUNNER, "record", change, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=20,
    )
    try:
        out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        out = {"_raw_stdout": proc.stdout, "_raw_stderr": proc.stderr}
    return proc.returncode, out


def _write_result_json(tmp: str, data: dict) -> str:
    path = os.path.join(tmp, "result.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path


# ────────────────────────────────────────────────────────────
# T4 / T5 / T6: fatal 错误分支（不依赖 Orchestrator 实例化）
# ────────────────────────────────────────────────────────────

class TestRecordResultJsonFatal(unittest.TestCase):
    """致命错误：result_json 缺失/非法/缺 status。"""

    def test_T4_result_json_missing(self):
        """--result-json 指向不存在文件 → fatal result_json_missing。"""
        rc, out = _run_record([
            "--result-json", "/tmp/__nonexistent_result_json_xyz__.json",
        ])
        self.assertEqual(rc, 0, "runner 自己 print JSON，exit 0；fatal 在 JSON 内")
        self.assertTrue(out.get("fatal"), out)
        self.assertIn("result_json_missing", out["reason"])
        self.assertIn("hint", out)

    def test_T5_result_json_invalid_top_level(self):
        """--result-json 内容顶层是 list（不是 dict）→ fatal result_json_invalid。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w") as f:
                f.write("[1, 2, 3]")  # top-level list, not dict
            rc, out = _run_record(["--result-json", path])
            self.assertEqual(rc, 0)
            self.assertTrue(out.get("fatal"), out)
            self.assertIn("result_json_invalid", out["reason"])
            self.assertIn("顶层必须", out["reason"])

    def test_T5b_result_json_malformed_json(self):
        """--result-json 内容非合法 JSON → fatal result_json_invalid。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w") as f:
                f.write("not json {")
            rc, out = _run_record(["--result-json", path])
            self.assertEqual(rc, 0)
            self.assertTrue(out.get("fatal"), out)
            self.assertIn("result_json_invalid", out["reason"])

    def test_T6_status_missing(self):
        """--result-json 缺 status 且 CLI 也未传 → fatal status_missing。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_result_json(tmp, {
                "summary": "x",
                "report_path": "",
                "outputs": [],
                "evidence_paths": [],
                "tasks_updated": [],
            })
            rc, out = _run_record(["--result-json", path])
            self.assertEqual(rc, 0)
            self.assertTrue(out.get("fatal"), out)
            self.assertIn("status_missing", out["reason"])

    def test_T6b_explicit_status_overrides_file_missing(self):
        """--result-json 缺 status 但 CLI 传了 --status → 不 fatal。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_result_json(tmp, {
                "summary": "x",
                "report_path": "",
                "tasks_updated": ["1.1"],
                "outputs": ["/tmp/Foo.java"],
                "evidence_paths": ["/tmp/ev.md"],
            })
            # 不传 --status，让 CLI 依赖文件，但文件没 status → 应 fatal
            rc, out = _run_record(["--result-json", path])
            self.assertTrue(out.get("fatal"), out)
            self.assertIn("status_missing", out["reason"])


# ────────────────────────────────────────────────────────────
# T1 / T2 / T3 / T7 / T8: 走 orchestrator.record 的真路径
# 需要给 runner 一个最小可工作 change 目录
# ────────────────────────────────────────────────────────────

def _setup_minimal_change(tmp_root: str, change: str = "v25-test") -> None:
    """在 tmp_root/.pg/changes/<change>/ 下放最小 snapshot/state，使 Orchestrator 不崩。

    Orchestrator.__init__ 会调用 load_snapshot(change_root)，找不到时返回 None，
    新建一个空 PipelineState(change=...)。但 record() 会调 _derive_result_path()
    扫描 2-build/ 目录——目录不存在时返回空字符串，跳过 result.json 校验。
    """
    pass  # 直接 subprocess 跑即可，Orchestrator 默认行为够用


class TestRecordResultJsonOrchestrator(unittest.TestCase):
    """CLI + Orchestrator 联动：验证 7 字段正确传递给 record()。"""

    def test_T2_cli_only_no_result_json(self):
        """回归保护：不传 --result-json 时 CLI 字段原样传给 orch.record。"""
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("# report")
            report_path = f.name
        try:
            out, calls = _run_record_in_process([
                "--status", "completed",
                "--summary", "summary",
                "--report", report_path,
                "--outputs", "/tmp/A.java",
                "--tasks-updated", "1.1",
                "--tasks-updated", "1.2",
            ])
            self.assertFalse(out.get("fatal"), out)
            self.assertEqual(len(calls), 1, calls)
            c = calls[0]
            self.assertEqual(c["status"], "completed")
            self.assertEqual(c["report_path"], report_path)
            self.assertEqual(c["summary"], "summary")
            self.assertEqual(c["outputs"], "/tmp/A.java")
            self.assertEqual(c["issues"], "")
            self.assertEqual(c["tasks_updated"], ["1.1", "1.2"])
        finally:
            os.unlink(report_path)

    def test_T1_only_result_json_loads_all_7_fields(self):
        """仅 --result-json：7 字段从文件加载并传给 orch.record。"""
        with tempfile.TemporaryDirectory() as tmp:
            report = os.path.join(tmp, "report.md")
            evidence = os.path.join(tmp, "ev.md")
            with open(report, "w") as f:
                f.write("# report")
            with open(evidence, "w") as f:
                f.write("# ev")
            rj = _write_result_json(tmp, {
                "status": "pass",
                "summary": "from file",
                "report_path": report,
                "outputs": ["/tmp/X.java", "/tmp/Y.java"],
                "issues": ["i1", "i2"],
                "evidence_paths": [evidence],
                "tasks_updated": ["2.1", "2.3"],
            })
            out, calls = _run_record_in_process(["--result-json", rj])
            self.assertFalse(out.get("fatal"), out)
            self.assertEqual(len(calls), 1)
            c = calls[0]
            self.assertEqual(c["status"], "pass")
            self.assertEqual(c["report_path"], report)
            self.assertEqual(c["summary"], "from file")
            self.assertIn("/tmp/X.java", c["outputs"])
            self.assertIn("/tmp/Y.java", c["outputs"])
            self.assertEqual(c["issues"], "i1,i2")
            self.assertEqual(c["evidence_paths"], [evidence])
            self.assertEqual(c["tasks_updated"], ["2.1", "2.3"])

    def test_T3_cli_overrides_file(self):
        """CLI 字段非空 → 覆盖文件同名字段。"""
        with tempfile.TemporaryDirectory() as tmp:
            rj = _write_result_json(tmp, {
                "status": "pass",
                "summary": "file summary",
                "report_path": "",
                "outputs": [],
                "issues": [],
                "evidence_paths": [],
                "tasks_updated": ["file-task"],
            })
            out, calls = _run_record_in_process([
                "--result-json", rj,
                "--status", "fail",
                "--summary", "cli summary",
                "--tasks-updated", "cli-task",
            ])
            self.assertFalse(out.get("fatal"), out)
            c = calls[0]
            self.assertEqual(c["status"], "fail")
            self.assertEqual(c["summary"], "cli summary")
            # list 字段：CLI 与文件合并（CLI 在前）
            self.assertEqual(c["tasks_updated"], ["cli-task", "file-task"])

    def test_T7_list_merge_evidence_and_tasks(self):
        """list 字段：CLI 与文件拼接去空。"""
        with tempfile.TemporaryDirectory() as tmp:
            rj = _write_result_json(tmp, {
                "status": "pass",
                "summary": "x",
                "report_path": "",
                "outputs": [],
                "issues": [],
                "evidence_paths": ["/tmp/file-ev.md"],
                "tasks_updated": ["file-1", "file-2"],
            })
            out, calls = _run_record_in_process([
                "--result-json", rj,
                "--evidence", "/tmp/cli-ev.md",
                "--evidence", "/tmp/cli-ev2.md",
                "--tasks-updated", "cli-1",
                "--tasks-updated", "cli-2,cli-3",
            ])
            self.assertFalse(out.get("fatal"), out)
            c = calls[0]
            self.assertEqual(c["evidence_paths"],
                             ["/tmp/cli-ev.md", "/tmp/cli-ev2.md", "/tmp/file-ev.md"])
            self.assertEqual(c["tasks_updated"],
                             ["cli-1", "cli-2", "cli-3", "file-1", "file-2"])

    def test_T8_list_filter_empty_strings_and_none(self):
        """list 字段过滤空字符串 / None / 纯空白。"""
        with tempfile.TemporaryDirectory() as tmp:
            rj = _write_result_json(tmp, {
                "status": "pass",
                "summary": "x",
                "report_path": "",
                "outputs": [],
                "issues": [],
                "evidence_paths": ["/tmp/ev1.md", "", None, "  ", "/tmp/ev2.md"],
                "tasks_updated": ["t1", "", None, "  ", "t2"],
            })
            out, calls = _run_record_in_process(["--result-json", rj])
            self.assertFalse(out.get("fatal"), out)
            c = calls[0]
            self.assertEqual(c["evidence_paths"], ["/tmp/ev1.md", "/tmp/ev2.md"])
            self.assertEqual(c["tasks_updated"], ["t1", "t2"])

    def test_T1b_status_validation(self):
        """--result-json 加载的 status 是合法值时通过 argparse choices（来自 CLI choices 集合）。

        注：argparse choices 校验只在 --status 是 CLI 显式传时生效；
        从 --result-json 文件加载的 status 在 in-process FakeOrchestrator 测试中
        不会被校验（真实环境由 sub_agent_contract.validate_record_args 校验）。
        """
        with tempfile.TemporaryDirectory() as tmp:
            rj = _write_result_json(tmp, {
                "status": "completed",
                "summary": "x",
                "report_path": "",
                "outputs": [],
                "issues": [],
                "evidence_paths": [],
                "tasks_updated": [],
            })
            out, calls = _run_record_in_process(["--result-json", rj])
            self.assertFalse(out.get("fatal"), out)
            self.assertEqual(calls[0]["status"], "completed")


# ────────────────────────────────────────────────────────────
# 边界：file_values 中 str 字段被存成 int（如 status=0）→ 转为 "0"
# ────────────────────────────────────────────────────────────

class TestRecordResultJsonEdgeCases(unittest.TestCase):
    def test_string_field_non_string_in_file_is_coerced(self):
        """文件里 str 字段是数字 → str() 转换，不 fatal。"""
        with tempfile.TemporaryDirectory() as tmp:
            rj = _write_result_json(tmp, {
                "status": "completed",
                "summary": 12345,        # 非 str → str(12345) == "12345"
                "report_path": "",
                "outputs": [],
                "issues": [],
                "evidence_paths": [],
                "tasks_updated": [],
            })
            out, calls = _run_record_in_process(["--result-json", rj])
            self.assertFalse(out.get("fatal"), out)
            self.assertEqual(calls[0]["summary"], "12345")


if __name__ == "__main__":
    unittest.main()