"""Unit tests for sub_agent_contract.py — v2.1 sub-agent 返回契约校验。"""

import os
import sys
import tempfile
import unittest

# 让测试可以 import pg-build-v2 scripts
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "scripts",
    ),
)

from pipeline.sub_agent_contract import validate_record_args, PHASE_RULES


class TestSubAgentContract(unittest.TestCase):
    """sub_agent_contract.validate_record_args 行为测试。"""

    def test_empty_summary_rejected(self):
        ok, reason = validate_record_args("test", "dev.backend", "completed", "", "/tmp/x", "")
        self.assertFalse(ok)
        self.assertIn("summary", reason)

    def test_summary_too_long_rejected(self):
        ok, reason = validate_record_args("test", "dev.backend", "completed", "x" * 201, "", "")
        self.assertFalse(ok)
        self.assertIn("200", reason)

    def test_invalid_status_rejected(self):
        ok, reason = validate_record_args("test", "dev.backend", "BOGUS", "summary", "", "")
        self.assertFalse(ok)
        self.assertIn("BOGUS", reason)

    def test_valid_test_phase_accepted(self):
        # test 阶段: 无需 evidence / report
        ok, reason = validate_record_args(
            "test", "dev.backend", "completed",
            "summary text", "/tmp/x", "/tmp/output.java",
            tasks_updated=["1.1"],
        )
        self.assertTrue(ok, reason)

    def test_valid_dev_phase_accepted(self):
        ok, reason = validate_record_args(
            "dev", "dev.backend", "completed",
            "summary text", "", "/tmp/output.java",
            tasks_updated=["2.1"],
        )
        self.assertTrue(ok, reason)

    def test_verify_missing_evidence_rejected(self):
        ok, reason = validate_record_args(
            "verify", "dev.backend", "completed",
            "summary text", "/tmp/nonexistent_xxx.md", "",
        )
        self.assertFalse(ok)
        # 可能因 report_missing 先失败，或 evidence_missing 失败
        self.assertTrue("evidence_missing" in reason or "report_missing" in reason)

    def test_verify_missing_report_rejected(self):
        ok, reason = validate_record_args(
            "verify", "dev.backend", "completed",
            "summary text", "", "/tmp/output.md",
        )
        self.assertFalse(ok)
        self.assertIn("report_path", reason)

    def test_gate_missing_report_rejected(self):
        ok, reason = validate_record_args(
            "gate", "dev.backend", "pass",
            "summary text", "", "/tmp/ev.md",
        )
        self.assertFalse(ok)
        self.assertIn("report_path", reason)

    def test_gate_report_nonexistent_rejected(self):
        ok, reason = validate_record_args(
            "gate", "dev.backend", "pass",
            "summary text", "/tmp/nonexistent_zzz.md", "/tmp/ev.md",
        )
        self.assertFalse(ok)
        self.assertIn("report_missing", reason)

    def test_final_gate_treated_like_gate(self):
        """track=final-gate 应与 phase=gate 同等校验。"""
        ok, reason = validate_record_args(
            "gate", "final-gate", "pass",
            "summary text", "", "",
        )
        self.assertFalse(ok)
        self.assertIn("evidence_missing", reason)

    def test_gate_with_valid_evidence_and_report(self):
        # 创建临时报告文件
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False
        ) as f:
            f.write("# PASS\n\nVerification complete.")
            tmp_path = f.name
        try:
            ok, reason = validate_record_args(
                "gate", "dev.backend", "pass",
                "summary text gate_score: 85, p0_failures: []",
                tmp_path, "/tmp/ev.md",
            )
            self.assertTrue(ok, reason)
        finally:
            os.unlink(tmp_path)

    def test_gate_missing_score_rejected(self):
        """v2.1: gate / final-gate summary 必须含 gate_score。"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False
        ) as f:
            f.write("# PASS")
            tmp_path = f.name
        try:
            ok, reason = validate_record_args(
                "gate", "dev.backend", "pass",
                "summary without score",
                tmp_path, "/tmp/ev.md",
            )
            self.assertFalse(ok)
            self.assertIn("gate_score", reason)
        finally:
            os.unlink(tmp_path)

    def test_gate_score_out_of_range_rejected(self):
        """gate_score 必须 0-100。"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False
        ) as f:
            f.write("# PASS")
            tmp_path = f.name
        try:
            ok, reason = validate_record_args(
                "gate", "dev.backend", "pass",
                "gate_score: 150",
                tmp_path, "/tmp/ev.md",
            )
            self.assertFalse(ok)
            self.assertIn("100", reason)
        finally:
            os.unlink(tmp_path)

    def test_parse_gate_score_happy(self):
        from pipeline.sub_agent_contract import parse_gate_score
        self.assertEqual(parse_gate_score("gate_score: 85, p0_failures: []"), 85)
        self.assertEqual(parse_gate_score("gate_score=92"), 92)
        self.assertEqual(parse_gate_score("final_score: 80, min_track_score: 75"), 80)
        self.assertEqual(parse_gate_score("no score here"), None)
        self.assertEqual(parse_gate_score("gate_score: abc"), None)

    def test_fix_phase_requires_report_and_outputs(self):
        # v2.2: fix 阶段需要 report + outputs；v2.3: 还需要 tasks_updated
        report = "/tmp/__test_fix_phase_report.md"
        try:
            with open(report, "w") as f:
                f.write("fix report")
            ok, reason = validate_record_args(
                "fix", "dev.backend", "completed",
                "summary text", report, "/tmp/output.java",
                tasks_updated=["V-backend-6"],
            )
            self.assertTrue(ok, reason)
        finally:
            if os.path.isfile(report):
                os.remove(report)

    def test_phase_rules_consistency(self):
        """PHASE_RULES 必须覆盖所有 phase。"""
        expected = {"test", "dev", "verify", "gate", "fix", "fix-gate", "simple"}
        self.assertEqual(set(PHASE_RULES.keys()), expected)

    # ============================================================
    # v2.1 新增：问题 8 — status 与 phase 必须兼容
    # ============================================================

    def test_gate_rejects_completed_status(self):
        """[v2.1] gate 阶段不接受 completed（gate 必须用 pass/fail）。"""
        ok, reason = validate_record_args(
            "gate", "dev.backend", "completed", "summary gate_score: 90, p0_failures: []",
            "/tmp/r.md", "/tmp/ev.md",
        )
        self.assertFalse(ok)
        self.assertIn("gate", reason)
        self.assertIn("completed", reason)

    def test_gate_rejects_escalate_status(self):
        """[v2.1] gate 阶段不接受 escalate。"""
        ok, reason = validate_record_args(
            "gate", "dev.backend", "escalate", "summary gate_score: 90, p0_failures: []",
            "/tmp/r.md", "/tmp/ev.md",
        )
        self.assertFalse(ok)

    def test_test_phase_rejects_pass_status(self):
        """[v2.1] test 阶段不接受 pass（test 必须用 completed/failed）。"""
        ok, reason = validate_record_args(
            "test", "dev.backend", "pass", "summary", "/tmp/r.md", "",
        )
        self.assertFalse(ok)
        self.assertIn("test", reason)

    def test_verify_accepts_escalate_status(self):
        """[v2.1] verify 阶段允许 escalate（用于触发 fix 循环）。"""
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# FAIL\nverify escalate")
            tmp_report = f.name
        try:
            ok, reason = validate_record_args(
                "verify", "dev.backend", "escalate", "summary text",
                tmp_report, "",
                evidence_paths=[tmp_report],  # v2.2: escalate 需要 evidence
                tasks_updated=["V-backend-6"],  # v2.3: escalate 必填
            )
            self.assertTrue(ok, reason)
        finally:
            os.unlink(tmp_report)

    def test_final_gate_accepts_only_pass_fail(self):
        """[v2.1] final-gate (track=final-gate, phase=gate) 只接受 pass/fail。"""
        ok, reason = validate_record_args(
            "gate", "final-gate", "completed",
            "summary text gate_score: 90, p0_failures: []", "/tmp/r.md", "/tmp/ev.md",
        )
        self.assertFalse(ok, "final-gate 不应接受 completed")
        self.assertIn("gate", reason)

    # ============================================================
    # v2.3 新增：tasks_updated 必填校验
    # ============================================================

    def test_test_phase_requires_tasks_updated(self):
        """[v2.3] test 阶段必填 tasks_updated。"""
        ok, reason = validate_record_args(
            "test", "dev.backend", "completed",
            "summary", "", "/tmp/output.java",
            tasks_updated=(),
        )
        self.assertFalse(ok)
        self.assertIn("tasks-updated", reason)
        self.assertIn("test", reason)

    def test_test_phase_blank_string_rejected(self):
        """[v2.3] test 阶段空字符串也算未填。"""
        ok, reason = validate_record_args(
            "test", "dev.backend", "completed",
            "summary", "", "/tmp/output.java",
            tasks_updated=["", "  "],
        )
        self.assertFalse(ok)
        self.assertIn("tasks-updated", reason)

    def test_test_phase_accepts_tasks_updated(self):
        ok, reason = validate_record_args(
            "test", "dev.backend", "completed",
            "summary", "", "/tmp/output.java",
            tasks_updated=["10.1", "10.2"],
        )
        self.assertTrue(ok, reason)

    def test_dev_phase_requires_tasks_updated(self):
        ok, reason = validate_record_args(
            "dev", "dev.backend", "completed",
            "summary", "", "/tmp/output.java",
            tasks_updated=(),
        )
        self.assertFalse(ok)
        self.assertIn("dev", reason)
        self.assertIn("tasks-updated", reason)

    def test_dev_phase_accepts_tasks_updated(self):
        ok, reason = validate_record_args(
            "dev", "dev.backend", "completed",
            "summary", "", "/tmp/output.java",
            tasks_updated=["2.1"],
        )
        self.assertTrue(ok, reason)

    def test_fix_phase_requires_tasks_updated(self):
        report = "/tmp/__test_fix_phase_tasks_report.md"
        try:
            with open(report, "w") as f:
                f.write("fix report")
            ok, reason = validate_record_args(
                "fix", "dev.backend", "completed",
                "summary", report, "/tmp/output.java",
                tasks_updated=(),
            )
            self.assertFalse(ok)
            self.assertIn("fix", reason)
            self.assertIn("tasks-updated", reason)
        finally:
            if os.path.isfile(report):
                os.remove(report)

    def test_fix_phase_accepts_v_id(self):
        report = "/tmp/__test_fix_phase_tasks_v_report.md"
        try:
            with open(report, "w") as f:
                f.write("fix report")
            ok, reason = validate_record_args(
                "fix", "dev.backend", "completed",
                "summary", report, "/tmp/output.java",
                tasks_updated=["V-backend-6"],
            )
            self.assertTrue(ok, reason)
        finally:
            if os.path.isfile(report):
                os.remove(report)

    def test_fix_gate_phase_requires_tasks_updated(self):
        report = "/tmp/__test_fix_gate_tasks_report.md"
        try:
            with open(report, "w") as f:
                f.write("fix-gate report")
            ok, reason = validate_record_args(
                "fix-gate", "dev.backend", "completed",
                "summary", report, "",
                evidence_paths=[report],
                tasks_updated=(),
            )
            self.assertFalse(ok)
            self.assertIn("fix-gate", reason)
            self.assertIn("tasks-updated", reason)
        finally:
            if os.path.isfile(report):
                os.remove(report)

    def test_simple_phase_no_tasks_updated_ok(self):
        ok, reason = validate_record_args(
            "simple", "dev.openapi-gen", "completed",
            "summary", "", "",
        )
        self.assertTrue(ok, reason)

    def test_verify_completed_no_tasks_updated_ok(self):
        """[v2.3] verify completed 不必填。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# PASS")
            tmp = f.name
        try:
            ok, reason = validate_record_args(
                "verify", "dev.backend", "completed",
                "summary", tmp, "",
                evidence_paths=[tmp],
            )
            self.assertTrue(ok, reason)
        finally:
            os.unlink(tmp)

    def test_verify_escalate_requires_tasks_updated(self):
        """[v2.3] verify escalate 必填。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# FAIL")
            tmp = f.name
        try:
            ok, reason = validate_record_args(
                "verify", "dev.backend", "escalate",
                "summary", tmp, "",
                evidence_paths=[tmp],
                tasks_updated=(),
            )
            self.assertFalse(ok)
            self.assertIn("escalate", reason)
            self.assertIn("tasks-updated", reason)
        finally:
            os.unlink(tmp)

    def test_gate_phase_no_tasks_updated_ok(self):
        """[v2.3] gate 不必填。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# PASS")
            tmp = f.name
        try:
            ok, reason = validate_record_args(
                "gate", "dev.backend", "pass",
                "summary gate_score: 90, p0_failures: []", tmp, "/tmp/ev.md",
            )
            self.assertTrue(ok, reason)
        finally:
            os.unlink(tmp)


if __name__ == "__main__":
    unittest.main()