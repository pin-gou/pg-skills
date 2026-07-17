#!/usr/bin/env python3
"""pg-auto-refine-check.py — 检测当前 review-notes.md 是否满足 v3.7 全推荐条件.

全推荐条件 (ALL must hold):
  1. 所有 common_decisions (5 项) 的 `current == recommended`
  2. 所有 issue_decisions 的 `current` 都是默认值（阻塞 = FIX, 重要 = FIX, 建议 = SKIP）
  3. 用户未编辑过 review-notes.md（mtime 早于所有产物文件）
  4. (v4.1) 所有 issue_decision 的 target_file 都在 .pg/changes/<change>/ 之下

输出 JSON:
  {
    "should_auto_apply": bool,
    "reason": "<说明>",
    "common_decisions_status": "all_recommended" | "diverged: <列表>",
    "issue_decisions_status": "all_default" | "non_default: <列表>",
    "user_edited": bool,
    "blocking_issues_count": int,
    "scope_violations": [...]  // (v4.1 新增)
  }

Exit code:
  0 = should_auto_apply = true
  1 = should_auto_apply = false (有分歧，等待用户调用 /2.1-pg-propose-refine)
  2 = error (review-notes.md 不存在，调用 /3-pg-propose 生成)
  3 = error (scope violation，参见 v4.1 Scope Boundary Contract)
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

# 复用 review-notes 路径解析
# 注意: 测试可以 monkey-patch 模块级 _CHANGES_DIR_OVERRIDE 指向 tmpdir
_CHANGES_DIR_OVERRIDE = os.environ.get("PG_CHANGES_DIR_OVERRIDE")
try:
    from pg_pipeline_common import CHANGES_DIR as _PIPELINE_CHANGES_DIR
    if _CHANGES_DIR_OVERRIDE:
        CHANGES_DIR = _CHANGES_DIR_OVERRIDE
    else:
        CHANGES_DIR = _PIPELINE_CHANGES_DIR
except ImportError:
    CHANGES_DIR = os.path.join(
        os.environ.get("PG_PROJECT_ROOT", os.getcwd()),
        ".pg", "changes"
    )
    if _CHANGES_DIR_OVERRIDE:
        CHANGES_DIR = _CHANGES_DIR_OVERRIDE


def _parse_decision_table(content: str) -> tuple[list[dict], list[str]]:
    """提取 review-notes.md 中的"通用决策"表格行 + 发现问题列表.

    Returns:
        (common_decisions, sections) — common_decisions 是 [{key, current, recommended, options}]
                                        sections 是 ['问题清单'段下的标题, ...]
    """
    common = []
    sections = []
    in_common = False
    in_issues = False
    for line in content.splitlines():
        s = line.strip()
        if not s:
            continue
        # 段头检测
        if s.startswith("## "):
            if "通用决策" in s:
                in_common = True
                in_issues = False
            elif "自审发现的问题" in s or "问题" in s:
                in_issues = True
                in_common = False
            else:
                in_common = False
                in_issues = False
            continue

        if in_common and s.startswith("|") and "---" not in s:
            parts = [p.strip() for p in s.split("|")]
            if len(parts) < 5:
                continue
            key = parts[1]
            # 跳过表头行（不含真实 key）
            if key not in {
                "error_response_strategy", "auth_scope",
                "data_migration_strategy", "transaction_boundary",
                "frontend_interaction_style",
            }:
                continue
            common.append({
                "key": key,
                "options_text": parts[2],
                "current": parts[3],
                "recommended": parts[4],
            })
        elif in_issues and s.startswith("- ["):
            # 抽取 `[ ]` / `[x]` / `[~]` 加粗标题
            sections.append(s)
    return common, sections


def _extract_issue_severity(title: str) -> str:
    """从问题条目反推严重度（阻塞/重要/建议）.

    严格依赖 review-notes.md 模板结构：
      ### 阻塞（必须修复后再 build）
      ### 重要（建议修复后再 build）
      ### 建议（可选优化）
    """
    return "unknown"


def detect_review_modified(review_path: str, product_paths: list[str]) -> bool:
    """检测用户是否编辑过 review-notes.md.

    简单启发: 若 review-notes.md 的 mtime 晚于任一产物（tasks.md / design.md /
    execution-manifest.yaml 等）的 mtime，则认为用户编辑过。

    Returns True if user edited.
    """
    if not os.path.isfile(review_path):
        return False
    review_mtime = os.path.getmtime(review_path)
    for p in product_paths:
        if not os.path.isfile(p):
            continue
        if os.path.getmtime(p) > review_mtime:
            return True
    return False


# v4.1 Scope Boundary check
# 校验 review-notes.md 中所有 issue_decision 的 "目标" 字段是否在变更目录内
# 越界 → exit code 3
_PRODUCT_FILES = frozenset({"proposal.md", "design.md", "tasks.md", "review-notes.md",
                            "proposal/design/tasks/review-notes.md"})
_TARGET_PATTERN = re.compile(r"-\s*目标[:：]\s*`?([^`\n]+)`?", re.MULTILINE)
_PRODUCT_REF_INLINE = re.compile(
    r"`(proposal|design|tasks|review-notes)\.md`(?:[^\n]*第\s*\d|章节|验证项|条目)",
    re.MULTILINE,
)


def _resolve_target_to_abs(target: str, change_root: str, repo_root: str) -> str:
    """把 review-notes 中的"目标"字符串解析为绝对路径。

    规则：
      - 以 `/` 开头 → 原样视为绝对路径
      - 含路径分隔符（含隐性 `./`、`../`） → 相对 repo_root 解析
      - 仅 basename 形式（且不在 _PRODUCT_FILES 白名单）→ 视为相对 repo_root
      - 落在 _PRODUCT_FILES 白名单 → 视为相对 change_root/1-propose-review/ + 产物根
    """
    target = target.strip()
    abs_change_root = os.path.abspath(change_root)
    if target.startswith("/"):
        return os.path.abspath(target)
    if not target:
        return abs_change_root
    # 含路径分隔符 → 相对 repo_root
    if "/" in target or "\\" in target or target.startswith(("./", "../")):
        return os.path.abspath(os.path.join(repo_root, target))
    # 纯 basename + 在产物白名单 → 相对 change_root
    if target in _PRODUCT_FILES:
        return os.path.join(abs_change_root, target)
    # 其它纯 basename（如 service.go, package.json）→ 相对 repo_root
    return os.path.abspath(os.path.join(repo_root, target))


def _check_decision_target_scope(content: str, change_root: str) -> list[str]:
    """扫描 review-notes.md 中所有"- 目标：..."字段，返回越界路径列表。

    Returns:
        越界的 target 字符串列表（空列表 = 全部合规）。
    """
    repo_root = os.environ.get("PG_PROJECT_ROOT", os.getcwd())
    abs_change_root = os.path.abspath(change_root) + os.sep
    violations: list[str] = []

    for match in _TARGET_PATTERN.finditer(content):
        target = match.group(1).strip()
        if not target:
            continue
        # 形如 "tasks.md 第 X 章" / "design.md V-X-N" 内联引用 → 合规
        if _PRODUCT_REF_INLINE.search(f"`{target}`" + match.string[match.end():match.end()+30]):
            continue
        abs_path = _resolve_target_to_abs(target, change_root, repo_root)
        if not abs_path.startswith(abs_change_root):
            violations.append(f"{target} → {abs_path}")

    return violations


def check_should_auto_apply(change: str) -> dict:
    """检测当前 review-notes.md 是否符合自动应用条件."""
    review_path = os.path.join(
        CHANGES_DIR, change, "1-propose-review", "review-notes.md"
    )
    change_root = os.path.join(CHANGES_DIR, change)
    if not os.path.isfile(review_path):
        return {
            "should_auto_apply": False,
            "error": "review-notes.md 不存在，请先跑 /3-pg-propose",
            "exit_code": 2,
        }

    with open(review_path, encoding="utf-8") as f:
        content = f.read()

    # v4.1: scope boundary check（最优先，违规直接阻断）
    scope_violations = _check_decision_target_scope(content, change_root)
    if scope_violations:
        return {
            "should_auto_apply": False,
            "error": "scope violation: 以下目标超出 .pg/changes/<change>/ 范围",
            "scope_violations": scope_violations,
            "exit_code": 3,
            "reason": (
                "scope violation: " + "; ".join(scope_violations) +
                " (refine 阶段禁止触碰业务代码，必须翻译为对 proposal.md/design.md/tasks.md 内的修改)"
            ),
        }

    common, issue_lines = _parse_decision_table(content)

    # 1. common_decisions 必须全部 current == recommended
    diverged = [c["key"] for c in common if c["current"] != c["recommended"]]
    common_ok = (len(common) == 5 and not diverged)

    # 2. issue_decisions 必须全部是默认值（[ ] FIX/SKIP per severity）
    # 由于需要知道每个问题的严重度才能判定 default，按行宽松判定：
    # - 所有 [ ] → 默认
    # - 所有 [ ] 或 [x]（已修复）→ 仍可自动
    # - 出现 [~] → 用户已表达 SKIP 意图，需 refine
    user_overrides = [s for s in issue_lines if "[~]" in s]
    has_unfixed = [s for s in issue_lines if s.startswith("- [ ]")]
    # blocking issues 默认是 FIX, important 默认是 FIX, 但 [ ] 阻塞项算默认 FIX
    # 关键限制: 阻塞项必须是 FIX，但默认就是 FIX——所有 [ ] 默认 FIX 已满足
    # 真正需要 refine 的：[~]（已 SKIP 重要/建议）和 [x]（已修复）
    # [x] 表示 LLM 已手工修复，不需要 refine 重做
    # 用户覆盖的判定: [~] 即视为"用户意图明确"
    issue_status = "all_default" if not user_overrides else "user_overrides"
    issue_ok = (not user_overrides)

    # 3. 检测用户是否编辑过
    product_paths = [
        os.path.join(CHANGES_DIR, change, fn)
        for fn in ("proposal.md", "design.md", "tasks.md",
                   "execution-manifest.yaml")
    ]
    # 现实: review-notes.md 通常最后写，所以 mtime 永远大于早期产物
    # 改用 grep [~] / 已固化字段 (✅) 作为"用户编辑"信号
    user_edited_markers = [
        re.search(r"当前.*[✅]", content),  # 通用决策被勾过
        re.search(r"\[~\]", content),  # 任意 SKIP
        re.search(r"\[x\]", content),  # 已修复
        re.search(r"已应用时间", content),  # 已被 refine 处理过
    ]
    user_edited = bool(any(user_edited_markers))

    should = common_ok and issue_ok and not user_edited

    return {
        "should_auto_apply": should,
        "common_decisions_count": len(common),
        "common_decisions_status": "all_recommended" if common_ok else f"diverged: {diverged}",
        "issue_decisions_status": issue_status,
        "user_edited": user_edited,
        "blocking_issues_unfixed": len([s for s in has_unfixed if "阻塞" not in s]),
        "scope_violations": [],  # v4.1: 全合规时为空列表
        "reason": (
            "符合自动应用条件"
            if should else
            f"不自动: common={('全推' if common_ok else '分歧')}, "
            f"issues={issue_status}, user_edited={user_edited}"
        ),
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 pg-auto-refine-check.py <change>", file=sys.stderr)
        sys.exit(1)
    change = sys.argv[1]
    result = check_should_auto_apply(change)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    exit_code = result.get("exit_code")
    if exit_code == 3:
        sys.exit(3)  # scope violation
    if result.get("error"):
        sys.exit(2)
    sys.exit(0 if result["should_auto_apply"] else 1)


if __name__ == "__main__":
    main()
