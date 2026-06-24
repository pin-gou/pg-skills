"""pg_context_chain.py — Context Chain 管理

替代 pg-context-chain.sh，被 pg-pipeline-runner.py 直接 import 调用。
"""

import os
from datetime import datetime, timezone, timedelta


# ============================================================
# Path resolution
# ============================================================

def _find_project_root():
    p = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        if (os.path.isfile(os.path.join(p, ".pg", "project.yaml"))
                or os.path.isfile(os.path.join(p, "pg-spec", "config.yaml"))):
            return p
        p = os.path.dirname(p)
    return os.getcwd()


PROJECT_ROOT = _find_project_root()
CHANGES_DIR = os.path.join(PROJECT_ROOT, ".pg", "changes")
APPLY_DIR = "2-build"
_MAX_FIX_CYCLES = 4


# ============================================================
# Helpers
# ============================================================

_SHANGHAI = timezone(timedelta(hours=8))


def _now():
    return datetime.now(_SHANGHAI).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def _chain_path(change):
    return os.path.join(CHANGES_DIR, change, APPLY_DIR, "context-chain.md")


def _state_path(change):
    return os.path.join(CHANGES_DIR, change, APPLY_DIR, ".context-chain.state")


def _write_state(change, key, value):
    path = _state_path(change)
    lines = []
    found = False
    if os.path.isfile(path):
        with open(path) as f:
            for line in f:
                line = line.rstrip("\n")
                if line.startswith(f"{key}="):
                    lines.append(f"{key}={value}")
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def _read_state(change, key):
    path = _state_path(change)
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith(f"{key}="):
                return line[len(key) + 1:]
    return None


# ============================================================
# Public API
# ============================================================

def init(change):
    path = _chain_path(change)
    if os.path.isfile(path):
        restart(change)
        return

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(f"# Context Chain - {change}\n\n")
        f.write("---\n")
        f.write("*此文件由编排器自动管理，请勿手动修改*\n\n")
    _write_state(change, "start_timestamp", _now())


def restart(change):
    _write_state(change, "start_timestamp", _now())
    ts = _now()
    with open(_chain_path(change), "a") as f:
        f.write(f"\n### {ts} - WORKFLOW RESTART\n")
        f.write("**状态**: RESTARTED\n")
        f.write("**说明**: 编排器重新执行 `/3-pg-build`，从第一个未完成项继续\n\n")


def sub_start(change, track, sub, fix_cycle=None):
    ts = _now()
    label = f"{track}:{sub}[{fix_cycle}/{_MAX_FIX_CYCLES}]" if fix_cycle else f"{track}:{sub}"
    with open(_chain_path(change), "a") as f:
        f.write(f"\n### {ts} - {label} START\n")
        f.write("**状态**: IN_PROGRESS\n\n")


def sub_end(change, track, sub, status, report="", summary="", outputs="", issues="", fix_cycle=None):
    ts = _now()
    label = f"{track}:{sub}[{fix_cycle}/{_MAX_FIX_CYCLES}]" if fix_cycle else f"{track}:{sub}"
    with open(_chain_path(change), "a") as f:
        f.write(f"\n### {ts} - {label} END\n")
        f.write(f"**状态**: {status}\n")
        if report:   f.write(f"**报告**: {report}\n")
        if summary:  f.write(f"**摘要**: {summary}\n")
        if outputs:  f.write(f"**输出文件**: {outputs}\n")
        if issues:   f.write(f"**问题**: {issues}\n")
        f.write("\n")


def phase_start(change, phase_id):
    ts = _now()
    with open(_chain_path(change), "a") as f:
        f.write(f"\n### {ts} - {phase_id} START\n")
        f.write("**状态**: IN_PROGRESS\n\n")


def phase_end(change, phase_id, summary=""):
    ts = _now()
    with open(_chain_path(change), "a") as f:
        f.write(f"\n### {ts} - {phase_id} END\n")
        f.write("**状态**: COMPLETED\n")
        f.write(f"**摘要**: {summary}\n\n")


def rollback_set(change, track, reason, source, level="path"):
    ts = _now()
    with open(_chain_path(change), "a") as f:
        f.write(f"\n## rollback_context: {track}\n")
        f.write(f"- timestamp: {ts}\n")
        f.write(f"- level: {level}\n")
        f.write(f"- reason: {reason}\n")
        f.write(f"- source: {source}\n")


def rollback_clear(change, track):
    path = _chain_path(change)
    if not os.path.isfile(path):
        return
    with open(path) as f:
        lines = f.readlines()
    new_lines = []
    in_block = False
    for line in lines:
        stripped = line.strip()
        if stripped == f"## rollback_context: {track}":
            in_block = True
            continue
        if in_block:
            if stripped.startswith("## ") or stripped.startswith("---"):
                in_block = False
                new_lines.append(line)
            continue
        if not in_block:
            new_lines.append(line)
    with open(path, "w") as f:
        f.writelines(new_lines)


def rollback_get(change, track):
    path = _chain_path(change)
    if not os.path.isfile(path):
        return {"found": False}
    with open(path) as f:
        lines = f.readlines()
    in_block = False
    block = {}
    for line in lines:
        stripped = line.strip()
        if stripped == f"## rollback_context: {track}":
            in_block = True
            continue
        if in_block:
            if stripped.startswith("## rollback_context:") or stripped.startswith("## "):
                break
            if stripped.startswith("- timestamp:"):
                block["timestamp"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("- level:"):
                block["level"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("- reason:"):
                block["reason"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("- source:"):
                block["source"] = stripped.split(":", 1)[1].strip()
    if block:
        block.setdefault("level", "path")
        block["found"] = True
        return block
    return {"found": False}


def workflow_complete(change, status):
    start_ts = _read_state(change, "start_timestamp")
    end_ts = _now()
    duration = ""
    if start_ts:
        try:
            fmt = "%Y-%m-%dT%H:%M:%S%z"
            start = datetime.strptime(start_ts.replace("+08:00", "+0800"), fmt)
            end = datetime.strptime(end_ts.replace("+08:00", "+0800"), fmt)
            elapsed = int((end - start).total_seconds())
            duration = f"{elapsed // 60}m {elapsed % 60}s"
        except (ValueError, OSError):
            pass
    with open(_chain_path(change), "a") as f:
        f.write(f"\n### {end_ts} - WORKFLOW COMPLETED\n")
        f.write(f"**状态**: {status}\n")
        f.write(f"**总耗时**: {duration}\n\n")


def ensure(change):
    if not os.path.isfile(_chain_path(change)):
        init(change)
