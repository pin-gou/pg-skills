#!/usr/bin/env python3
"""migrate-define-summary.py — v1.3 兼容性迁移工具.

旧格式的 define-summary.yaml (id="V-NNN" 单一数字形态) 在 v1.3 后 schema 强制要求
track_id 字段 + 推荐 V-{track_id}-{seq} 形态. 本脚本把旧 id 改写为新格式.

Usage:
    python3 migrate-define-summary.py <change-dir> [--track <id>] [--dry-run]

行为:
    - 扫描 <change-dir>/0-define/define-summary.yaml
    - 对 verification_needs[].id 匹配 '^V-\\d+$' 的条目:
        - 若 id 已经含 track 前缀 (V-{track_id}-N), 跳过
        - 否则改写为 V-{track_id}-{old_numeric_id_digits}
    - 给 verification_needs[].track_id 缺失的条目补上 (命令行 --track 必填,
      或从 define-summary.yaml 中已有 track_id 推断)

Options:
    --track <id>       track id 必填 (除非 define-summary 中已含 track_id)
    --dry-run          只打印 diff, 不写盘
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML 未安装", file=sys.stderr)
    sys.exit(2)


_OLD_V_RE = re.compile(r"^V-(\d+)$")
_NEW_V_RE = re.compile(r"^V-[a-z][a-z0-9-]*-\d+$")


def migrate(doc: dict, track_id: str | None) -> tuple[dict, list[str]]:
    """返回 (新 doc, 改动列表 human-readable). 同一对象可能被修改."""
    changes: list[str] = []
    vns = doc.get("verification_needs") or []
    inferred_track = track_id
    if inferred_track is None:
        # 尝试从已有 track_id 字段推断
        for v in vns:
            tid = v.get("track_id")
            if isinstance(tid, str) and tid:
                inferred_track = tid
                break
    if inferred_track is None:
        print("ERROR: 无法推断 track_id, 必须 --track 显式指定 (或 define-summary 中已含 track_id)",
              file=sys.stderr)
        sys.exit(2)

    for i, v in enumerate(vns):
        if not isinstance(v, dict):
            continue
        old_id = v.get("id")
        if isinstance(old_id, str):
            m_old = _OLD_V_RE.match(old_id)
            if m_old:
                digits = m_old.group(1)
                new_id = f"V-{inferred_track}-{digits}"
                v["id"] = new_id
                changes.append(f"verification_needs[{i}].id: {old_id} -> {new_id}")
            elif not _NEW_V_RE.match(old_id):
                # 既不是旧 V-NNN 也不是新 V-{track}-N → 跳过 (避免误改非法 id)
                changes.append(f"verification_needs[{i}].id: {old_id} (skipped, not in old/new pattern)")
        if "track_id" not in v:
            v["track_id"] = inferred_track
            changes.append(f"verification_needs[{i}].track_id: (added) {inferred_track}")
        elif v["track_id"] != inferred_track:
            # 已存在但与命令行 --track 不一致: 优先尊重现有 track_id, 不覆盖
            changes.append(f"verification_needs[{i}].track_id: {v['track_id']} (kept, --track ignored)")
    return doc, changes


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("change_dir", help=".pg/changes/<change>/ 路径")
    p.add_argument("--track", help="track_id (当 define-summary 中没有时必填)")
    p.add_argument("--dry-run", action="store_true", help="只打印 diff 不写盘")
    args = p.parse_args()

    ds_path = Path(args.change_dir) / "0-define" / "define-summary.yaml"
    if not ds_path.is_file():
        print(f"ERROR: {ds_path} 不存在", file=sys.stderr)
        return 1

    doc = yaml.safe_load(ds_path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        print(f"ERROR: {ds_path} 顶层不是 object", file=sys.stderr)
        return 1

    new_doc, changes = migrate(doc, args.track)
    if not changes:
        print(f"OK: {ds_path} 无需迁移 (所有 V-* 已为 V-{{track}}-N 形态或无 verification_needs)")
        return 0

    print(f"=== Changes for {ds_path} ===")
    for c in changes:
        print(f"  {c}")

    if args.dry_run:
        print("(dry-run: 未写盘)")
        return 0

    # 写回 (允许 _orig 备份)
    backup = ds_path.with_suffix(ds_path.suffix + ".bak")
    backup.write_text(ds_path.read_text(encoding="utf-8"), encoding="utf-8")
    ds_path.write_text(
        yaml.safe_dump(new_doc, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"Wrote {ds_path}; backup at {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())