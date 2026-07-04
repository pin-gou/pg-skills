#!/usr/bin/env python3
"""pg-pipeline-runner.py — pg-build-v2 CLI 入口。

用法：
  python3 pg-pipeline-runner.py bootstrap <change>
  python3 pg-pipeline-runner.py next <change>
  python3 pg-pipeline-runner.py record <change> --status <status> [--report <path>] [--summary <文本>] [--outputs <p1,p2>] [--issues <i1,i2>] [--evidence <e>] [--tasks-updated <t>]
  python3 pg-pipeline-runner.py progress <change>
  python3 pg-pipeline-runner.py env-action <change> --phase <prepare_env|clean_env> --stage <stage> --env <env> [--timeout <seconds>]
  python3 pg-pipeline-runner.py env-action-result <change> --phase <prepare_env|clean_env> --stage <stage> --env <env> --success <true|false> [--log-path <path>] [--exit-code <code>] [--started-ts <ts>] [--error <msg>]
"""

from __future__ import annotations

import argparse
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
        ea_parser = argparse.ArgumentParser(prog="pg-pipeline-runner.py env-action", add_help=False)
        ea_parser.add_argument("change", nargs="?", default=change)
        ea_parser.add_argument("--phase", required=True, choices=("prepare_env", "clean_env"),
                               help="环境 hook 阶段 (prepare_env|clean_env)")
        ea_parser.add_argument("--stage", required=True, help="阶段名 (如 dev)")
        ea_parser.add_argument("--env", required=True, help="环境名 (如 dev-local)")
        ea_parser.add_argument("--timeout", type=int, default=None, help="hook 超时秒数")
        ea_args = ea_parser.parse_args(sys.argv[3:])
        result = _bootstrap.cli_env_action(
            change, ea_args.phase, ea_args.stage, ea_args.env,
            hook_timeout_seconds=ea_args.timeout,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

# ── env-action-result 命令（编排器在 bash 执行完 env hook 后调用）──
    if command == "env-action-result":
        ear_parser = argparse.ArgumentParser(prog="pg-pipeline-runner.py env-action-result", add_help=False)
        ear_parser.add_argument("change", nargs="?", default=change)
        ear_parser.add_argument("--phase", required=True, choices=("prepare_env", "clean_env"),
                                help="环境 hook 阶段")
        ear_parser.add_argument("--stage", required=True, help="阶段名 (如 dev)")
        ear_parser.add_argument("--env", required=True, help="环境名 (如 dev-local)")
        ear_parser.add_argument("--success", required=True,
                                help="布尔值 true|false — hook 是否成功执行")
        ear_parser.add_argument("--log-path", default="", help="hook 日志文件路径")
        ear_parser.add_argument("--exit-code", type=int, default=None, help="hook 进程退出码")
        ear_parser.add_argument("--started-ts", default="", help="hook 启动时间戳")
        ear_parser.add_argument("--error", default="", help="hook 错误信息")
        ear_args = ear_parser.parse_args(sys.argv[3:])

        success_str = ear_args.success.lower()
        if success_str in ("true", "1"):
            success = True
        elif success_str in ("false", "0"):
            success = False
        else:
            _usage(f"无效 success: {ear_args.success}。\n"
                   f"  提示：success 是布尔值 (true|false)，表示 hook 是否成功执行。\n"
                   f"       与 --status (completed/failed/...) 含义不同，\n"
                   f"       与 sub-agent 返回 JSON 的 status 字段也不同。\n"
                   f"  有效值: true | false")
            sys.exit(1)
        result = _bootstrap.cli_env_action_result(
            change, ear_args.phase, ear_args.stage, ear_args.env,
            success=success,
            log_path=ear_args.log_path,
            exit_code=ear_args.exit_code,
            started_event_ts=ear_args.started_ts or None,
            error=ear_args.error or None,
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
        rec_parser = argparse.ArgumentParser(prog="pg-pipeline-runner.py record", add_help=False)
        rec_parser.add_argument("change", nargs="?", default=change)
        rec_parser.add_argument("--status", required=True, choices=tuple(sorted(VALID_STATUSES)),
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
    print("  python3 pg-pipeline-runner.py bootstrap <change>                                          # 执行 bootstrap 副作用", file=sys.stderr)
    print("  python3 pg-pipeline-runner.py next <change>                                              # 获取下一步 action", file=sys.stderr)
    print("  python3 pg-pipeline-runner.py record <change> --status <status> [--report <path>] [--summary <文本>] [--outputs <...>] [--issues <...>] [--evidence <...>] [--tasks-updated <...>]", file=sys.stderr)
    print("  python3 pg-pipeline-runner.py progress <change>                                           # 查看进度", file=sys.stderr)
    print("  python3 pg-pipeline-runner.py env-action <change> --phase prepare_env|clean_env --stage <stage> --env <env> [--timeout <秒>]", file=sys.stderr)
    print("  python3 pg-pipeline-runner.py env-action-result <change> --phase prepare_env|clean_env --stage <stage> --env <env> --success true|false [--log-path <path>] [--exit-code <code>] [--started-ts <ts>] [--error <msg>]", file=sys.stderr)
    print("  python3 pg-pipeline-runner.py replay <change>                                             # 从 events 重建 state", file=sys.stderr)
    print("  python3 pg-pipeline-runner.py verify-replay <change>                                      # 对比 snapshot vs replay", file=sys.stderr)
    print(file=sys.stderr)
    print("record --status: completed | failed | escalate | pass | fail", file=sys.stderr)
    print("env-action --phase: prepare_env | clean_env", file=sys.stderr)
    print("env-action-result --success: true | false   # 布尔值，表示 hook 是否成功执行", file=sys.stderr)
    print(file=sys.stderr)
    print("# 字段语义注解（避免混淆）：", file=sys.stderr)
    print("#   record --status              事件 outcome (completed/failed/escalate/pass/fail)", file=sys.stderr)
    print("#   env-action-result --success  hook 执行结果 (true|false 布尔值)", file=sys.stderr)
    print("#   sub-agent 返回 status        任务执行结果 (completed/failed/escalate/pass/fail)", file=sys.stderr)


if __name__ == "__main__":
    main()
