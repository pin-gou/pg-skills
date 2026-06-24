#!/usr/bin/env python3
"""
pg-regression-summary.py — 将多个 suite 的 JSON 问题清单合并为人类可读的汇总报告。

Usage:
  python3 pg-regression-summary.py --suites .pg/regression/*.json --out .pg/regression/summary-<datetime>.md
"""

import json
import sys
from pathlib import Path


def _make_issue_id(title, suite):
    import hashlib
    slug = ''.join(c if c.isalnum() else '-' for c in title.lower())[:30]
    slug = '-'.join(x for x in slug.split('-') if x)[:30]
    h = hashlib.md5(f"{suite}:{title}".encode()).hexdigest()[:6]
    return f"{slug}-{h}"


def build_summary(suites: list[dict]) -> str:
    lines = []

    all_issues = []
    suite_stats = {}

    for data in suites:
        suite = data.get("suite", "unknown")
        issues = data.get("issues", [])
        skipped = data.get("skipped_targets", [])
        suite_stats[suite] = {"issues": len(issues), "skipped": len(skipped)}
        for iss in issues:
            iss["_suite"] = suite
            all_issues.append(iss)
            if not iss.get("id"):
                iss["id"] = _make_issue_id(iss.get("title", ""), suite)

    total = len(all_issues)
    t_skipped = sum(s["skipped"] for s in suite_stats.values())
    t_suites = len(suites)

    lines.append("# pg-regression 汇总报告\n")
    lines.append(f"**生成时间**: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**套件数**: {t_suites}")
    lines.append(f"**生产代码问题总数**: {total}")
    lines.append(f"**已知问题跳过**: {t_skipped}\n")

    lines.append("---\n")

    # Per-suite breakdown
    lines.append("## 按套件分布\n")
    lines.append("| 套件 | 生产代码问题 | 已知问题跳过 |")
    lines.append("|------|-------------|-------------|")
    for s_name, stats in sorted(suite_stats.items()):
        lines.append(f"| {s_name} | {stats['issues']} | {stats['skipped']} |")
    lines.append("")

    if total == 0:
        lines.append("**无生产代码问题，无需修复。**")
        return "\n".join(lines)

    lines.append("---\n")
    lines.append("## 问题清单\n")

    for iss in all_issues:
        title = iss.get("title", "")
        suite = iss.get("_suite", "")
        component = iss.get("component", "")
        file_path = iss.get("file", "")
        expected = iss.get("expected", "")
        actual = iss.get("actual", "")

        lines.append(f"### {title}\n")
        lines.append(f"- **套件**: {suite}")
        lines.append(f"- **组件**: {component}")
        if file_path:
            lines.append(f"- **文件**: {file_path}")
        if expected:
            lines.append(f"- **期望**: {expected}")
        if actual:
            lines.append(f"- **实际**: {actual}")
        lines.append("")

    lines.append("---\n")
    lines.append("## 输出目录规范\n")
    lines.append("- 修复结果: `.pg/regression/results/<datetime>-<suite>-<id>-pr<N>.json`")
    lines.append("- 汇总报告: `.pg/regression/summary-<datetime>.md`")
    lines.append("- 问题清单: `.pg/regression/<suite>.json`")

    return "\n".join(lines)


def main():
    args = sys.argv[1:]
    out_path = None
    suite_paths = []

    i = 0
    while i < len(args):
        if args[i] == "--out" and i + 1 < len(args):
            out_path = args[i + 1]
            i += 2
        elif args[i].startswith("--suites") and i + 1 < len(args):
            i += 1
            while i < len(args) and not args[i].startswith("-"):
                suite_paths.append(args[i])
                i += 1
        elif args[i].startswith("--"):
            i += 2
        else:
            suite_paths.append(args[i])
            i += 1

    if not suite_paths:
        print("Usage: pg-regression-summary.py --suites <json files...> --out <output.md>", file=sys.stderr)
        print("  Or:  pg-regression-summary.py .pg/regression/*.json --out <output.md>", file=sys.stderr)
        sys.exit(1)

    suites = []
    for p in suite_paths:
        fp = Path(p)
        if not fp.exists():
            print(f"Skipping non-existent: {fp}", file=sys.stderr)
            continue
        data = json.loads(fp.read_text(encoding="utf-8"))
        suites.append(data)

    markdown = build_summary(suites)

    if out_path:
        Path(out_path).write_text(markdown, encoding="utf-8")
        print(f"✅ 汇总报告已写入: {out_path}")
    else:
        print(markdown)


if __name__ == "__main__":
    main()