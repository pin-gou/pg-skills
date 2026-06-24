#!/usr/bin/env python3
"""pg-archive.py — Move a change directory to .pg/changes/archive/.

Shared implementation used by:
  - pg-archive SKILL  (manual archive after failure or abandonment)
  - pg-build runner  (auto-archive on final-gate pass)

Naming: YYYY-MM-DD-<change-name>, with .N suffix on collision.

Outputs JSON to stdout, no side effects beyond the directory move.

Usage:
  python3 pg-archive.py move <change-name> [--project-root <path>]
    Move .pg/changes/<change-name>/ to archive/.
    Prints: {"ok": true|false, "target_name": ..., "src": ..., "target": ...}
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime


def find_project_root(start):
    p = os.path.abspath(start)
    for _ in range(6):
        if os.path.isfile(os.path.join(p, ".pg", "project.yaml")):
            return p
        p = os.path.dirname(p)
    return os.getcwd()


def move_to_archive(change, project_root):
    src = os.path.join(project_root, ".pg", "changes", change)
    if not os.path.isdir(src):
        return {
            "ok": False,
            "reason": f"源目录不存在: {src}",
            "src": src,
        }

    archive_root = os.path.join(project_root, ".pg", "changes", "archive")
    try:
        os.makedirs(archive_root, exist_ok=True)
    except OSError as e:
        return {
            "ok": False,
            "reason": f"无法创建归档根目录 {archive_root}: {e}",
            "src": src,
        }

    archive_date = datetime.now().strftime("%Y-%m-%d")
    base = f"{archive_date}-{change}"
    target = os.path.join(archive_root, base)
    suffix = 0
    while os.path.exists(target):
        suffix += 1
        target = os.path.join(archive_root, f"{base}.{suffix}")

    try:
        shutil.move(src, target)
    except (shutil.Error, OSError) as e:
        return {
            "ok": False,
            "reason": f"移动失败: {e}",
            "src": src,
            "target": target,
        }

    return {
        "ok": True,
        "target_name": os.path.basename(target),
        "src": os.path.relpath(src, project_root),
        "target": os.path.relpath(target, project_root),
    }


def main():
    parser = argparse.ArgumentParser(
        description="pg-archive 共享脚本（手动/自动归档均使用）"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_move = sub.add_parser("move", help="移动 change 目录到 archive/")
    p_move.add_argument("change", help="change 名称")
    p_move.add_argument(
        "--project-root",
        default=None,
        help="项目根目录（默认自动向上查找 .pg/project.yaml）",
    )

    args = parser.parse_args()

    if args.cmd == "move":
        project_root = args.project_root
        if not project_root:
            project_root = find_project_root(os.getcwd())
        if not os.path.isfile(os.path.join(project_root, ".pg", "project.yaml")):
            result = {
                "ok": False,
                "reason": f"找不到 .pg/project.yaml: project_root={project_root}",
            }
            print(json.dumps(result, ensure_ascii=False))
            sys.exit(2)

        result = move_to_archive(args.change, project_root)
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
