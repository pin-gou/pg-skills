#!/usr/bin/env python3
"""pg-pipeline-runner.py — pg-build CLI 入口。

用法：
  python3 pg-pipeline-runner.py bootstrap <change>
  python3 pg-pipeline-runner.py reset <change>            # 手动清除 terminal failed 状态（自动场景见 bootstrap）
  python3 pg-pipeline-runner.py next <change>
  python3 pg-pipeline-runner.py record <change> --status <status> [--report <path>] [--summary <文本>] [--outputs <p1,p2>] [--issues <i1,i2>] [--evidence <e>] [--tasks-updated <t1,t2,...|--tasks-updated t1 --tasks-updated t2>]
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


VALID_COMMANDS = {"next", "record", "progress", "replay", "verify-replay", "bootstrap", "env-action", "env-action-result", "reset"}
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

    # ── reset 命令（独立命令，manual 入口，与 bootstrap 自动 reset 走同一函数）──
    # 默认场景：bootstrap 会自动检测 workflow_failed 并 reset，无需手动调用。
    # 本命令仅供编排器 / 排查场景需要显式 reset 时使用。
    if command == "reset":
        result = _bootstrap.cli_auto_reset(change)
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
        rec_parser.add_argument("--status", default="",
                                choices=tuple(sorted(VALID_STATUSES)) + ("",),
                                help="record status: completed|failed|escalate|pass|fail"
                                     "（v2.5: 可从 --result-json 加载，CLI 留空即可）")
        rec_parser.add_argument("--report", default="",
                                help="报告文件绝对路径（v2.5: 可从 --result-json 加载）")
        rec_parser.add_argument("--summary", default="",
                                help="<=200 字的一句话摘要（v2.5: 可从 --result-json 加载）")
        rec_parser.add_argument("--outputs", default="",
                                help="产物文件列表，逗号分隔（v2.5: 可从 --result-json 加载）")
        rec_parser.add_argument("--issues", default="",
                                help="问题列表，逗号分隔（v2.5: 可从 --result-json 加载）")
        rec_parser.add_argument("--evidence", action="append", default=[],
                                help="证据文件绝对路径（可多次传；v2.5: 可从 --result-json 加载并合并）")
        rec_parser.add_argument("--tasks-updated", action="append", default=[],
                                dest="tasks_updated",
                                help="已更新的 task_id。支持两种格式：1) 逗号分隔: --tasks-updated \"1.1,1.2,1.3\"  2) 多次传参: --tasks-updated 1.1 --tasks-updated 1.2。两种格式可混用（v2.5: 可从 --result-json 加载并合并）")
        # v2.5: 从 result.json 一次性加载 7 字段（CLI 显式参数优先级高于文件内容）
        rec_parser.add_argument("--result-json", default="",
                                dest="result_json",
                                help="v2.5: 从此 result.json 文件加载 7 字段填充 record 参数"
                                     "（status/summary/report_path/outputs/issues/evidence_paths/tasks_updated）。"
                                     "CLI 显式非空参数优先于文件内容。可与现有参数混用。")
        # v2.7: design.md fault 标记
        rec_parser.add_argument("--design-md-fault", action="store_true", default=False,
                                help="v2.7: fix-review 检测到 design.md 文档层缺陷")
        rec_parser.add_argument("--design-md-fault-location", default="",
                                help="v2.7: 文档缺陷位置 (file:line)")
        rec_args = rec_parser.parse_args(sys.argv[3:])

        # ── v2.5: 加载 --result-json（如指定）──
        file_values: dict[str, Any] = {}
        if rec_args.result_json:
            if not os.path.isfile(rec_args.result_json):
                print(json.dumps({
                    "action": "error", "fatal": True,
                    "reason": (
                        f"result_json_missing: '{rec_args.result_json}' 不存在或不是文件。"
                        f"v2.5: --result-json 必须指向 sub-agent 已落盘的 result.json。"
                        f"  编排器应使用 dispatch action 中的 expected_result_path。"
                    ),
                    "hint": (
                        "确认 sub-agent 是否调用了 "
                        "pg-build-result --output-path <path> --require-output。"
                        "如未落盘，可让编排器改回 v2.4 显式 CLI 形式"
                        "（--status --summary --report --evidence --tasks-updated ...）。"
                    ),
                }, ensure_ascii=False))
                return
            try:
                with open(rec_args.result_json, "r", encoding="utf-8") as _f:
                    _data = json.load(_f)
            except (OSError, json.JSONDecodeError) as _e:
                print(json.dumps({
                    "action": "error", "fatal": True,
                    "reason": (
                        f"result_json_invalid: '{rec_args.result_json}' JSON 解析失败: {_e}。"
                        f"v2.5: --result-json 必须是合法 JSON 对象。"
                    ),
                    "hint": "检查文件是否被覆写或损坏；让 sub-agent 重新调用 pg-build-result 落盘。",
                }, ensure_ascii=False))
                return
            if not isinstance(_data, dict):
                print(json.dumps({
                    "action": "error", "fatal": True,
                    "reason": (
                        f"result_json_invalid: '{rec_args.result_json}' 顶层必须是 JSON object，"
                        f"实际得到 {type(_data).__name__}。"
                    ),
                    "hint": "pg-build-result 输出的 JSON 顶层就是 object；检查文件来源。",
                }, ensure_ascii=False))
                return
            file_values = _data

        # ── v2.5: 7 字段合并（CLI 非空 > 文件 > 默认空）──
        def _merge_str(key: str, cli_val: str) -> str:
            """字符串字段：CLI 非空（strip 后） > 文件值 > 默认空。

            outputs/issues 在文件中可能以 list 形式存储（pg-build-result 输出），
            此时 join 成逗号分隔字符串，与 orchestrator.record() 的 outputs/issues 参数签名一致。
            """
            if cli_val and cli_val.strip():
                return cli_val
            v = file_values.get(key)
            if isinstance(v, list):
                return ",".join(str(x).strip() for x in v if x is not None and str(x).strip())
            if isinstance(v, str) and v.strip():
                return v
            if v is not None and not isinstance(v, str):
                return str(v)
            return ""

        def _merge_list(key: str, cli_list: list[str]) -> list[str]:
            """list 字段：CLI + 文件拼接（CLI 在前），去空字符串。"""
            merged: list[str] = []
            for x in cli_list:
                if x and str(x).strip():
                    merged.append(str(x).strip())
            file_v = file_values.get(key)
            if isinstance(file_v, list):
                for x in file_v:
                    if x is not None and str(x).strip():
                        merged.append(str(x).strip())
            return merged

        status = _merge_str("status", rec_args.status)
        report_path = _merge_str("report_path", rec_args.report)
        summary = _merge_str("summary", rec_args.summary)
        outputs = _merge_str("outputs", rec_args.outputs)
        issues = _merge_str("issues", rec_args.issues)
        evidence_paths = _merge_list("evidence_paths", rec_args.evidence)
        # tasks_updated 的 CLI 端还要做逗号分隔归一化，先归一再合并
        normalized_tasks_cli: list[str] = []
        for item in rec_args.tasks_updated:
            for part in item.split(","):
                part = part.strip()
                if part:
                    normalized_tasks_cli.append(part)
        tasks_updated = _merge_list("tasks_updated", normalized_tasks_cli)

        # ── v2.7: design_md_fault 从文件加载（CLI 优先级高于文件）──
        design_md_fault = rec_args.design_md_fault
        design_md_fault_location = rec_args.design_md_fault_location
        if not design_md_fault:
            design_md_fault = file_values.get("design_md_fault", False)
        if not design_md_fault_location:
            design_md_fault_location = file_values.get("design_md_fault_location", "")

        # ── v3.x: design_drift 从文件加载 ──
        design_drift_file = file_values.get("design_drift", "")
        design_drift = json.dumps(design_drift_file) if isinstance(design_drift_file, dict) else str(design_drift_file)

        # ── status 兜底必填（CLI/文件都没有 → fatal）──
        if not status:
            print(json.dumps({
                "action": "error", "fatal": True,
                "reason": (
                    "status_missing: --status 与 result.json.status 均为空。"
                    "v2.5: 编排器必须显式 --status 或在 --result-json 文件中提供 status 字段。"
                ),
                "hint": "传 --status completed|failed|escalate|pass|fail 之一。",
            }, ensure_ascii=False))
            return

        # —report 不存在时立刻退出（不调 orchestrator）
        if report_path and not os.path.isfile(report_path):
            print(json.dumps({
                "action": "error", "fatal": True,
                "reason": f"report_missing: '{report_path}' 不存在或不是文件",
            }))
            return

        result = orch.record(
            status, report_path, summary,
            outputs, issues,
            evidence_paths=evidence_paths,
            tasks_updated=tasks_updated,
            design_md_fault=design_md_fault,
            design_md_fault_location=design_md_fault_location,
            design_drift=design_drift,
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
    print("  python3 pg-pipeline-runner.py record <change> --status <status> [--report <path>] [--summary <文本>] [--outputs <...>] [--issues <...>] [--evidence <...>] [--tasks-updated <t1,t2,...|--tasks-updated t1 --tasks-updated t2>] [--design-md-fault] [--design-md-fault-location <file:line>]", file=sys.stderr)
    print("  python3 pg-pipeline-runner.py record <change> --result-json <result.json 路径>     # v2.5: 从 sub-agent 落盘的 result.json 加载 7 字段", file=sys.stderr)
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
    print("# --tasks-updated 支持两种传参格式：", file=sys.stderr)
    print("#   1. 逗号分隔: --tasks-updated \"1.1,1.2,1.3\"", file=sys.stderr)
    print("#   2. 多次传参: --tasks-updated 1.1 --tasks-updated 1.2", file=sys.stderr)
    print("#   两种格式可在同一命令中混用", file=sys.stderr)
    print(file=sys.stderr)
    print("# 字段语义注解（避免混淆）：", file=sys.stderr)
    print("#   record --status              事件 outcome (completed/failed/escalate/pass/fail)", file=sys.stderr)
    print("#   env-action-result --success  hook 执行结果 (true|false 布尔值)", file=sys.stderr)
    print("#   sub-agent 返回 status        任务执行结果 (completed/failed/escalate/pass/fail)", file=sys.stderr)


if __name__ == "__main__":
    main()
