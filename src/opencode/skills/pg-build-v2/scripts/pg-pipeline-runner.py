#!/usr/bin/env python3
"""pg-pipeline-runner.py — pg-build-v2 CLI 入口。

用法：
  python3 pg-pipeline-runner.py next <change>
  python3 pg-pipeline-runner.py record <change> <status> [report_path] [summary] [outputs] [issues]
  python3 pg-pipeline-runner.py progress <change>
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

# 确保 scripts/ 在 sys.path 中
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from pipeline.orchestrator import Orchestrator
from pipeline.replay import verify_snapshot_matches_replay


VALID_COMMANDS = {"next", "record", "progress", "replay", "verify-replay"}
VALID_STATUSES = {"completed", "failed", "escalate", "pass", "fail"}


def main() -> None:
    if len(sys.argv) < 2:
        _usage("缺少命令")
        sys.exit(1)

    command = sys.argv[1]

    if command not in VALID_COMMANDS:
        _usage(f"未知命令: {command}")
        sys.exit(1)

    if len(sys.argv) < 3:
        _usage(f"缺少 <change> 参数")
        sys.exit(1)

    change_arg = sys.argv[2]
    # v2.1: 若 change 含 /, 提取最后一段作为显示名（Orchestrator 会处理路径）
    change = os.path.basename(change_arg.rstrip("/")) if "/" in change_arg else change_arg

    # v2.1: replay / verify-replay 命令强制走 events 重建
    use_replay = command in ("replay", "verify-replay")

    orch = Orchestrator(change_arg, use_replay=use_replay)

    result: dict[str, Any] = {}
    if command == "next":
        result = orch.next()

    elif command == "replay":
        # 强制从 events 重建并返回当前 state
        result = {
            "command": "replay",
            "change": change,
            "loaded_via": orch._loaded_via,
            "state": orch.state.to_dict(),
        }

    elif command == "verify-replay":
        # 对比 snapshot vs replay 结果
        ok, message = verify_snapshot_matches_replay(orch.change_root)
        result = {
            "command": "verify-replay",
            "change": change,
            "consistent": ok,
            "message": message,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    elif command == "record":
        if len(sys.argv) < 4:
            _usage("record 命令缺少 <status> 参数")
            sys.exit(1)
        status = sys.argv[3]
        if status not in VALID_STATUSES:
            _usage(f"无效 status: {status}，有效值: {', '.join(sorted(VALID_STATUSES))}")
            sys.exit(1)
        report_path = sys.argv[4] if len(sys.argv) > 4 else ""
        summary = sys.argv[5] if len(sys.argv) > 5 else ""
        outputs = sys.argv[6] if len(sys.argv) > 6 else ""
        issues = sys.argv[7] if len(sys.argv) > 7 else ""
        result = orch.record(status, report_path, summary, outputs, issues)

    elif command == "progress":
        result = orch.progress()

    elif command == "replay":
        # 强制从 events 重建并返回当前 state
        result = {
            "command": "replay",
            "change": change,
            "loaded_via": orch._loaded_via,
            "state": orch.state.to_dict(),
        }

    elif command == "verify-replay":
        # 对比 snapshot vs replay 结果
        ok, message = verify_snapshot_matches_replay(orch.change_root)
        result = {
            "command": "verify-replay",
            "change": change,
            "consistent": ok,
            "message": message,
        }

    # 输出 JSON
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _usage(msg: str = "") -> None:
    if msg:
        print(f"错误: {msg}", file=sys.stderr)
    print("用法:", file=sys.stderr)
    print("  python3 pg-pipeline-runner.py next <change>           # 获取下一步 action", file=sys.stderr)
    print("  python3 pg-pipeline-runner.py record <change> <status> [report_path] [summary] [outputs] [issues]", file=sys.stderr)
    print("  python3 pg-pipeline-runner.py progress <change>        # 查看进度", file=sys.stderr)
    print("  python3 pg-pipeline-runner.py replay <change>          # v2.1: 从 events 重建 state", file=sys.stderr)
    print("  python3 pg-pipeline-runner.py verify-replay <change>   # v2.1: 对比 snapshot vs replay", file=sys.stderr)
    print(file=sys.stderr)
    print("status: completed | failed | escalate | pass | fail", file=sys.stderr)


if __name__ == "__main__":
    main()