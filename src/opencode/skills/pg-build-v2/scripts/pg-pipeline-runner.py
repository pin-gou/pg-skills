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

# 确保 scripts/ 在 sys.path 中
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from pipeline.orchestrator import Orchestrator


VALID_COMMANDS = {"next", "record", "progress"}
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

    change = sys.argv[2]
    orch = Orchestrator(change)

    if command == "next":
        result = orch.next()

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

    # 输出 JSON
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _usage(msg: str = "") -> None:
    if msg:
        print(f"错误: {msg}", file=sys.stderr)
    print("用法:", file=sys.stderr)
    print("  python3 pg-pipeline-runner.py next <change>", file=sys.stderr)
    print("  python3 pg-pipeline-runner.py record <change> <status> [report_path] [summary] [outputs] [issues]", file=sys.stderr)
    print("  python3 pg-pipeline-runner.py progress <change>", file=sys.stderr)
    print(file=sys.stderr)
    print("status: completed | failed | escalate | pass | fail", file=sys.stderr)


if __name__ == "__main__":
    main()