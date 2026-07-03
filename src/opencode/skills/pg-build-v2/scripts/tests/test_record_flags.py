"""Unit tests for record CLI flags — v2.2 argparse 模式 + 新校验。"""

import os
import sys
import tempfile
import unittest

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "scripts",
    ),
)

from pipeline.sub_agent_contract import validate_record_args, PHASE_RULES


class TestRecordFlagsDevOutputsRequired(unittest.TestCase):
    """dev 阶段 --outputs 必填检查。"""

    def test_dev_empty_outputs_rejected(self):
        ok, reason = validate_record_args(
            "dev", "dev.backend", "completed", "summary", "", "",
        )
        self.assertFalse(ok)
        self.assertIn("outputs", reason)

    def test_dev_with_outputs_accepted(self):
        ok, reason = validate_record_args(
            "dev", "dev.backend", "completed", "summary", "", "/tmp/Foo.java",
        )
        self.assertTrue(ok, reason)


class TestRecordFlagsTestOutputsRequired(unittest.TestCase):
    """test 阶段 --outputs 必填检查。"""

    def test_test_empty_outputs_rejected(self):
        ok, reason = validate_record_args(
            "test", "dev.backend", "completed", "summary", "", "",
        )
        self.assertFalse(ok)
        self.assertIn("outputs", reason)

    def test_test_with_outputs_accepted(self):
        ok, reason = validate_record_args(
            "test", "dev.backend", "completed", "summary", "", "/tmp/Test.java",
        )
        self.assertTrue(ok, reason)


class TestRecordFlagsFixRequired(unittest.TestCase):
    """fix 阶段 --report 必填 + --outputs 必填检查。"""

    def test_fix_no_report_rejected(self):
        ok, reason = validate_record_args(
            "fix", "dev.backend", "completed", "summary", "", "/tmp/fix.java",
        )
        self.assertFalse(ok)
        self.assertIn("report", reason)

    def test_fix_no_outputs_rejected(self):
        report = "/tmp/__test_fix_report.md"
        try:
            with open(report, "w") as f:
                f.write("fix report")
            ok, reason = validate_record_args(
                "fix", "dev.backend", "completed", "summary", report, "",
            )
            self.assertFalse(ok)
            self.assertIn("outputs", reason)
        finally:
            if os.path.isfile(report):
                os.remove(report)

    def test_fix_report_missing_rejected(self):
        ok, reason = validate_record_args(
            "fix", "dev.backend", "completed", "summary",
            "/tmp/__nonexistent_fix_report_xxx.md", "/tmp/fix.java",
        )
        self.assertFalse(ok)
        self.assertIn("report_missing", reason)


class TestRecordFlagsReportPathCheck(unittest.TestCase):
    """--report 路径不存在时的错误信息。"""

    def test_report_missing_error_message(self):
        ok, reason = validate_record_args(
            "verify", "dev.backend", "completed", "summary",
            "/tmp/__nonexistent_report_xxx.md", "",
        )
        self.assertFalse(ok)
        self.assertIn("report_missing", reason)
        self.assertIn("不存在", reason)


class TestRecordFlagsTasksUpdated(unittest.TestCase):
    """escalate 时 --tasks-updated 相关检查。"""

    def test_escalate_without_evidence_rejected(self):
        ok, reason = validate_record_args(
            "verify", "dev.backend", "escalate", "V-4 FAIL", "", "",
        )
        self.assertFalse(ok)
        self.assertIn("evidence", reason)

    def test_escalate_with_evidence_accepted(self):
        report = "/tmp/__test_escalate_verify.md"
        try:
            with open(report, "w") as f:
                f.write("verify report")
            ok, reason = validate_record_args(
                "verify", "dev.backend", "escalate",
                "V-4 FAIL", report, "/tmp/output.java",
                evidence_paths=[report],
            )
            self.assertTrue(ok, reason)
        finally:
            if os.path.isfile(report):
                os.remove(report)


class TestRecordFlagsSimpleTrack(unittest.TestCase):
    """simple 阶段不需要 outputs 强制。"""

    def test_simple_empty_outputs_accepted(self):
        ok, reason = validate_record_args(
            "simple", "dev.openapi-gen", "completed", "openapi gen ok", "", "",
        )
        self.assertTrue(ok, reason)

    def test_simple_with_outputs_accepted(self):
        ok, reason = validate_record_args(
            "simple", "dev.openapi-gen", "completed", "openapi gen ok", "", "/tmp/client.ts",
        )
        self.assertTrue(ok, reason)


if __name__ == "__main__":
    unittest.main()