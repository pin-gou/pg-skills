"""orchestrator._derive_result_path 测试 (v2.4 新增)。"""
import os
import sys
import tempfile
import unittest

# 让脚本能从任意 cwd 调用
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(_THIS_DIR)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from pipeline.state import PipelineState, TrackState, PhaseState  # noqa: E402
from pipeline.orchestrator import _derive_result_path  # noqa: E402


class TestDeriveResultPath(unittest.TestCase):
    """v2.4: dispatch_file → result JSON 路径派生函数测试。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.change_root = os.path.join(self.tmpdir, ".pg", "changes", "test-change")
        self.build_dir = os.path.join(self.change_root, "2-build")
        os.makedirs(self.build_dir, exist_ok=True)
        # mock CHANGES_DIR
        import pipeline.orchestrator as orch
        self._orig_changes_dir = orch.CHANGES_DIR
        orch.CHANGES_DIR = os.path.join(self.tmpdir, ".pg", "changes")

    def tearDown(self):
        import pipeline.orchestrator as orch
        orch.CHANGES_DIR = self._orig_changes_dir
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_state(self) -> PipelineState:
        return PipelineState(change="test-change")

    def _touch_dispatch(self, filename: str):
        with open(os.path.join(self.build_dir, filename), "w") as f:
            f.write("# dispatch\n")

    def test_dev_backend_dev_result_path(self):
        """普通 phase 派生：002-dev.backend-dev-dispatch.md → 002-dev.backend-dev-result.json"""
        self._touch_dispatch("002-dev.backend-dev-dispatch.md")
        state = self._make_state()
        path = _derive_result_path(state, "dev.backend", "dev")
        self.assertTrue(path.endswith("002-dev.backend-dev-result.json"),
                        f"got: {path}")

    def test_fix_cycle_2_adds_cycle_suffix(self):
        """fix cycle > 1：018-dev.frontend-fix-dispatch-2.md → 018-dev.frontend-fix-result-2.json"""
        self._touch_dispatch("018-dev.frontend-fix-dispatch-2.md")
        state = self._make_state()
        path = _derive_result_path(state, "dev.frontend", "fix")
        self.assertTrue(path.endswith("018-dev.frontend-fix-result-2.json"),
                        f"got: {path}")

    def test_final_gate_path(self):
        """final-gate：028-final-gate-gate-dispatch.md → 028-final-gate-gate-result.json"""
        self._touch_dispatch("028-final-gate-gate-dispatch.md")
        state = self._make_state()
        path = _derive_result_path(state, "final-gate", "gate")
        self.assertTrue(path.endswith("028-final-gate-gate-result.json"),
                        f"got: {path}")

    def test_latest_seq_wins(self):
        """多个 dispatch 时取最大 seq。"""
        self._touch_dispatch("002-dev.backend-dev-dispatch.md")
        self._touch_dispatch("005-dev.backend-dev-dispatch.md")
        state = self._make_state()
        path = _derive_result_path(state, "dev.backend", "dev")
        self.assertTrue(path.endswith("005-dev.backend-dev-result.json"),
                        f"got: {path}")

    def test_no_match_returns_empty(self):
        """无匹配 dispatch_file → 返回空字符串。"""
        state = self._make_state()
        path = _derive_result_path(state, "dev.unknown", "dev")
        self.assertEqual(path, "")

    def test_empty_track_returns_empty(self):
        """track 为空 → 返回空。"""
        state = self._make_state()
        path = _derive_result_path(state, "", "dev")
        self.assertEqual(path, "")


if __name__ == "__main__":
    unittest.main()