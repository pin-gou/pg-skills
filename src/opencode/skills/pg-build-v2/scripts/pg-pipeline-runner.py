#!/usr/bin/env python3
"""pg-pipeline-runner.py — pg-build-v2 CLI 入口。

用法：
  python3 pg-pipeline-runner.py bootstrap <change>
  python3 pg-pipeline-runner.py next <change>
  python3 pg-pipeline-runner.py record <change> <status> [report_path] [summary] [outputs] [issues]
  python3 pg-pipeline-runner.py progress <change>
  python3 pg-pipeline-runner.py env-action <change> <phase> <stage> <env> [hook_timeout_seconds]
  python3 pg-pipeline-runner.py env-action-result <change> <phase> <stage> <env> <ok> [log_path] [exit_code] [started_ts] [error]
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
from pipeline.events import STATUSES_ALL
import bootstrap as _bootstrap


VALID_COMMANDS = {"next", "record", "progress", "replay", "verify-replay", "bootstrap", "env-action", "env-action-result"}
VALID_STATUSES = STATUSES_ALL


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
    change = os.path.basename(change_arg.rstrip("/")) if "/" in change_arg else change_arg

    # ── bootstrap 命令（独立命令，不依赖 Orchestrator）──
    if command == "bootstrap":
        result = _bootstrap.cli_bootstrap(change)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # ── env-action 命令（独立命令，不依赖 Orchestrator）──
    # v2.1.1: 只返回 plan，**不执行**。编排器按 plan 自己 bash 执行。
    if command == "env-action":
        if len(sys.argv) < 5:
            _usage("env-action 命令缺少参数: <change> <phase> <stage> <env> [hook_timeout_seconds]")
            sys.exit(1)
        phase_name = sys.argv[3]
        if phase_name not in ("prepare_env", "clean_env"):
            _usage(f"无效 phase: {phase_name}，有效值: prepare_env | clean_env")
            sys.exit(1)
        stage_name = sys.argv[4]
        env_name = sys.argv[5] if len(sys.argv) > 5 else ""
        hook_timeout = int(sys.argv[6]) if len(sys.argv) > 6 and sys.argv[6] else None
        result = _bootstrap.cli_env_action(
            change, phase_name, stage_name, env_name,
            hook_timeout_seconds=hook_timeout,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # ── env-action-result 命令（编排器在 bash 执行完 env hook 后调用）──
    if command == "env-action-result":
        if len(sys.argv) < 7:
            _usage("env-action-result 命令缺少参数: <change> <phase> <stage> <env> <ok> [log_path] [exit_code] [started_ts] [error]")
            sys.exit(1)
        phase_name = sys.argv[3]
        if phase_name not in ("prepare_env", "clean_env"):
            _usage(f"无效 phase: {phase_name}，有效值: prepare_env | clean_env")
            sys.exit(1)
        stage_name = sys.argv[4]
        env_name = sys.argv[5]
        ok_str = sys.argv[6]
        if ok_str not in ("ok", "failed"):
            _usage(f"无效 ok: {ok_str}，有效值: ok | failed")
            sys.exit(1)
        ok = (ok_str == "ok")
        log_path = sys.argv[7] if len(sys.argv) > 7 else ""
        exit_code = int(sys.argv[8]) if len(sys.argv) > 8 and sys.argv[8] else None
        started_ts = sys.argv[9] if len(sys.argv) > 9 else ""
        error = sys.argv[10] if len(sys.argv) > 10 else ""
        result = _bootstrap.cli_env_action_result(
            change, phase_name, stage_name, env_name,
            ok=ok,
            log_path=log_path,
            exit_code=exit_code,
            started_event_ts=started_ts or None,
            error=error or None,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # ── 以下命令需要 Orchestrator ──
    use_replay = command in ("replay", "verify-replay")
    orch = Orchestrator(change_arg, use_replay=use_replay)

    result: dict[str, Any] = {}
    if command == "next":
        result = orch.next()

    elif command == "replay":
        result = {
            "command": "replay",
            "change": change,
            "loaded_via": orch._loaded_via,
            "state": orch.state.to_dict(),
        }

    elif command == "verify-replay":
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
        # v2.2: argparse 风格 — 仅支持 --flags
        from argparse import ArgumentParser as _ArgParser
        rec_parser = _ArgParser(prog="pg-pipeline-runner.py record", add_help=False)
        rec_parser.add_argument("status", nargs="?", default="",
                                help="record status: completed|failed|escalate|pass|fail")
        rec_parser.add_argument("--report", default="",
                                help="报告文件绝对路径")
        rec_parser.add_argument("--summary", default="",
                                help="<=200 字的一句话摘要")
        rec_parser.add_argument("--outputs", default="",
                                help="产物文件列表，逗号分隔")
        rec_parser.add_argument("--issues", default="",
                                help="问题列表，逗号分隔")
        rec_parser.add_argument("--evidence", action="append", default=[],
                                help="证据文件绝对路径（可多次传）")
        rec_parser.add_argument("--tasks-updated", action="append", default=[],
                                dest="tasks_updated",
                                help="已更新的 task_id（可多次传）")
        rec_args = rec_parser.parse_args(sys.argv[3:])

        status = rec_args.status
        if not status:
            _usage("record 命令缺少 <status> 参数")
            sys.exit(1)
        if status not in VALID_STATUSES:
            _usage(f"无效 status: {status}，有效值: {', '.join(sorted(VALID_STATUSES))}")
            sys.exit(1)

        # —report 不存在时立刻退出（不调 orchestrator）
        report_path = rec_args.report
        if report_path and not os.path.isfile(report_path):
            print(json.dumps({
                "action": "error", "fatal": True,
                "reason": f"report_missing: '{report_path}' 不存在或不是文件",
            }))
            return

        result = orch.record(
            status, report_path, rec_args.summary,
            rec_args.outputs, rec_args.issues,
            evidence_paths=rec_args.evidence,
            tasks_updated=rec_args.tasks_updated,
        )

    elif command == "progress":
        result = orch.progress()

    print(json.dumps(result, ensure_ascii=False, indent=2))


def _usage(msg: str = "") -> None:
    if msg:
        print(f"错误: {msg}", file=sys.stderr)
    print("用法:", file=sys.stderr)
    print("  python3 pg-pipeline-runner.py bootstrap <change>       # 执行 bootstrap 副作用（不含 env hook） + 检测配置", file=sys.stderr)
    print("  python3 pg-pipeline-runner.py next <change>           # 获取下一步 action", file=sys.stderr)
    print("  python3 pg-pipeline-runner.py record <change> <status> [report_path] [summary] [outputs] [issues]", file=sys.stderr)
    print("  python3 pg-pipeline-runner.py progress <change>        # 查看进度", file=sys.stderr)
    print("  python3 pg-pipeline-runner.py env-action <change> <phase> <stage> <env> [hook_timeout_seconds] # 返回 env hook plan（不执行）", file=sys.stderr)
    print("  python3 pg-pipeline-runner.py env-action-result <change> <phase> <stage> <env> <ok> [log_path] [exit_code] [started_ts] [error] # env hook 执行完上报", file=sys.stderr)
    print("  python3 pg-pipeline-runner.py replay <change>          # v2.1: 从 events 重建 state", file=sys.stderr)
    print("  python3 pg-pipeline-runner.py verify-replay <change>   # v2.1: 对比 snapshot vs replay", file=sys.stderr)
    print(file=sys.stderr)
    print("status: completed | failed | escalate | pass | fail", file=sys.stderr)
    print("env-action phase: prepare_env | clean_env", file=sys.stderr)
    print("env-action-result ok: ok | failed", file=sys.stderr)


if __name__ == "__main__":
    main()
