#!/usr/bin/env python3
"""pg-gen-tasks-skeleton.py — Mechanical skeleton generator for tasks.md.

Pure-function CLI: zero LLM dependency, zero heuristics beyond path-glob
and keyword matching. Generates a tasks.md skeleton where:

  - Only stages listed in `--selected-stages` produce headings (if provided).
  - Within a stage, only tracks that are in `--affected-tracks` produce headings.
  - Simple tracks in affected_tracks produce 1 heading; standard tracks produce 4
    (test / dev / verify / gate).
  - final-gate section is always appended regardless of selected_stages.
  - Each heading carries an HTML comment block documenting the
    on_conditions evaluation for the stage / track it belongs to.
  - Top of file contains the environment selection block quote.
  - No Evidence block is generated for verify sections.

Also emits a sibling `on-conditions-eval.md` file under
`.pg/changes/<change>/1-propose-review/` so the LLM can later (in stage 3
self-review) merge those evaluations into review-notes.md without having
to re-derive them from the proposal.

Usage:
    python3 pg-gen-tasks-skeleton.py \\
        --change <change-name> \\
        --proposal-md <path/to/proposal.md> \\
        --affected-tracks <track1,track2,...> \\
        --environment "<stage1>→<env1>,<stage2>→<env2>,..." \\
        [--selected-stages "<stage1>,<stage2>,..."] \\
        [--output-tasks <path>] \\
        [--output-eval <path>]

Exit code: 0 on success, 1 on usage/config errors.
"""

import argparse
import json
import os
import re
import sys

try:
    import yaml
except ImportError:
    print('{"error": "PyYAML is required. Install with: pip install pyyaml"}',
          file=sys.stderr)
    sys.exit(1)

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

from pg_pipeline_common import (
    PROJECT_ROOT,
    CHANGES_DIR,
    CONFIG_PATH,
    load_config,
    get_track_type,
)


# ============================================================
# Constants
# ============================================================

STANDARD_SUBS = [
    ("test",  lambda stage_name, test_key: f"{stage_name} 测试先行（{test_key}）"),
    ("dev",   lambda stage_name, test_key: "实现开发"),
    ("review", lambda stage_name, test_key: "静态代码审查"),
    ("verify", lambda stage_name, test_key: f"{stage_name} 集成验证"),
    ("gate",  lambda stage_name, test_key: f"{stage_name} 门控审查"),
]


# ============================================================
# Argument parsing
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate tasks.md skeleton + on-conditions eval template.")
    parser.add_argument("--change", required=True,
                        help="change name (kebab-case)")
    parser.add_argument("--proposal-md", required=True,
                        help="path to proposal.md (used for keyword extraction)")
    parser.add_argument("--affected-tracks", required=True,
                        help="comma-separated list of affected track IDs")
    parser.add_argument("--environment", required=True,
                        help="environment map: '<stage>→<env>,<stage>→<env>,...'")
    parser.add_argument("--output-tasks", default=None,
                        help="output tasks.md path (default: .pg/changes/<change>/tasks.md)")
    parser.add_argument("--output-eval", default=None,
                         help="output on-conditions-eval.md path "
                              "(default: .pg/changes/<change>/1-propose-review/on-conditions-eval.md)")
    parser.add_argument("--selected-stages", default="",
                         help="comma-separated list of stage names to include "
                              "(e.g. 'dev'). Only stages in this list and their "
                              "affected tracks generate sections. "
                              "Empty = include all stages (backward compatible).")
    return parser.parse_args()


def parse_env_map(env_arg: str) -> dict[str, str]:
    """Parse environment map arg into {stage_name: env_name}.

    Accepts both '→' (full-width arrow) and '->' (ASCII) as separators
    between stage and env, and ',' or '，' between entries.
    """
    result = {}
    for entry in re.split(r"[,，]", env_arg):
        entry = entry.strip()
        if not entry:
            continue
        m = re.match(r"(.+?)\s*(?:→|->|➜)\s*(.+)", entry)
        if not m:
            raise ValueError(f"无法解析 environment 项: {entry!r} "
                             f"(期望格式: <stage>→<env>)")
        result[m.group(1).strip()] = m.group(2).strip()
    return result


# ============================================================
# Proposal text analysis (mechanical)
# ============================================================

def extract_globs_from_proposal(proposal_text: str) -> list[str]:
    """Extract glob-like paths from proposal text.

    Looks for backtick-wrapped strings containing a slash or asterisk.
    Also extracts '### 包含' list items like '- **xxx**: description' when
    description mentions a path.

    Returns deduped list preserving order of first occurrence.
    """
    seen = set()
    out = []
    candidates = []

    for m in re.finditer(r"`([^`\n]+)`", proposal_text):
        body = m.group(1).strip()
        if "/" in body or "*" in body:
            candidates.append(body)

    for m in re.finditer(r"\*\*[^*\n]+\*\*\s*[:：]\s*([^\n]+)", proposal_text):
        body = m.group(1).strip()
        body = body.strip("`").rstrip(",，").rstrip(".")
        if "/" in body or "*" in body:
            candidates.append(body)

    for cand in candidates:
        cand = cand.strip()
        if not cand or cand in seen:
            continue
        seen.add(cand)
        out.append(cand)
    return out


def read_proposal_text(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


# ============================================================
# on_conditions mechanical evaluation
# ============================================================

def extract_globs_from_rule(rule: str) -> list[str]:
    """Extract glob patterns from a natural-language rule string.

    Heuristics: any token containing '/' or ending with '**' is treated
    as a glob. Also splits on whitespace and keeps tokens that look like
    file paths.
    """
    globs = []
    for tok in re.split(r"[\s,，。;；\"'`]+", rule):
        if "/" in tok or tok.endswith("**") or tok.startswith("**"):
            globs.append(tok)
    return globs


def extract_keywords_from_rule(rule: str) -> list[str]:
    """Extract semantic keywords from a natural-language rule.

    Strategy: split rule into individual characters, then greedily extract
    the longest content-words by scanning past stop phrases. This handles
    Chinese word-boundary-less text where multiple stop phrases concatenate.

    Skips: glob tokens, ASCII tokens without letters, single chars, and
    all stop phrases from a curated list.
    """
    stop_phrases = {
        "本变更", "本stage", "本track", "任一", "包含", "描述",
        "修改", "新增", "涉及", "命中", "是否", "以下", "情况", "当",
        "时", "则", "的", "了", "在", "和", "与", "或", "为", "是",
        "对", "一个", "所有", "每个", "以", "由", "被", "可",
        "打开", "关闭", "启用", "禁用", "忽略", "执行", "支持",
        "激活", "写入", "设置", "调整", "增加", "减少", "改动",
    }
    keywords = []
    # First pass: split by whitespace and major punctuation
    tokens = re.split(r"[\s,，。;；\"'`()()【】\[\]/\\*]+", rule)
    for tok in tokens:
        if not tok:
            continue
        if len(tok) < 2:
            continue
        if tok in stop_phrases:
            continue
        if tok.isascii() and not re.search(r"[a-zA-Z]", tok):
            continue
        # For Chinese-heavy tokens, try to peel off leading/trailing stop chars
        while tok and any(tok.startswith(sp) for sp in stop_phrases):
            for sp in stop_phrases:
                if tok.startswith(sp):
                    tok = tok[len(sp):]
                    break
        while tok and any(tok.endswith(sp) for sp in stop_phrases):
            for sp in stop_phrases:
                if tok.endswith(sp):
                    tok = tok[:-len(sp)]
                    break
        if tok and len(tok) >= 2 and tok not in stop_phrases:
            keywords.append(tok)
    return keywords


def check_glob_match(rule: str, affected_paths: list[str]) -> bool:
    """Return True if any affected_path matches any glob extracted from rule."""
    globs = extract_globs_from_rule(rule)
    if not globs or not affected_paths:
        return False
    import fnmatch
    for glob in globs:
        for path in affected_paths:
            if fnmatch.fnmatch(path, glob) or fnmatch.fnmatch(path, glob.rstrip("/") + "/**"):
                return True
            if glob.endswith("/**"):
                if path.startswith(glob[:-3]):
                    return True
            if glob in path:
                return True
    return False


def check_keyword_match(rule: str, proposal_text: str) -> bool:
    """Return True if any keyword extracted from rule appears in proposal_text."""
    keywords = extract_keywords_from_rule(rule)
    if not keywords or not proposal_text:
        return False
    for kw in keywords:
        if kw in proposal_text:
            return True
    return False


def evaluate_on_conditions(rule: str, affected_paths: list[str],
                            proposal_text: str) -> dict:
    """Evaluate a single on_conditions rule mechanically.

    Returns dict with rule / path_hit / semantic_hit / recommendation.
    """
    path_hit = check_glob_match(rule, affected_paths)
    semantic_hit = check_keyword_match(rule, proposal_text)
    matched = path_hit or semantic_hit
    return {
        "rule": rule,
        "path_hit": path_hit,
        "semantic_hit": semantic_hit,
        "matched": matched,
    }


# ============================================================
# Skeleton generation
# ============================================================

def build_sections(config: dict, affected_tracks: set,
                   selected_stages: set[str]) -> list[dict]:
    """Build the section list filtered by selected_stages and affected_tracks.

    - Only stages whose name is in selected_stages (or all if empty) are included.
    - Within a stage, only tracks that are in affected_tracks produce headings.
    - final-gate is always appended.

    v3.x 升级（code-review 阶段适配）：
      - standard track 的 sub 数量按 tracks.<id>.code_review_enabled 决定
        - enabled=true → 5 sub（test / dev / review / verify / gate）
        - enabled=false → 4 sub（test / dev / verify / gate）
      - 章节号 N 跨 change 不一致（已接受的硬冲突）

    Returns list of dicts with keys:
        n, stage, track, sub, is_simple, is_affected, label, env
    """
    sections = []
    all_stages = config.get("stages") or []
    tracks_cfg = config.get("tracks") or {}

    # 1) Filter stages by selected_stages
    if selected_stages:
        stages = [s for s in all_stages if s.get("name") in selected_stages]
    else:
        stages = list(all_stages)

    N = 1
    for stage in stages:
        stage_name = stage["name"]
        test_key = stage.get("test_key", "unit")

        for track_id in stage.get("tracks") or []:
            # 2) Skip tracks not in affected_tracks
            if track_id not in affected_tracks:
                continue

            track_type = get_track_type(config, track_id)
            is_simple = (track_type == "phase")

            if is_simple:
                label = f"{stage_name} {track_id}"
                sections.append({
                    "n": N,
                    "stage": stage_name,
                    "track": track_id,
                    "sub": None,
                    "is_simple": True,
                    "is_affected": True,
                    "label": label,
                    "env": None,
                })
                N += 1
            else:
                # v3.x: 动态 4/5 sub 决定
                track_cfg = tracks_cfg.get(track_id) or {}
                code_review_enabled = bool(track_cfg.get("code_review_enabled", True))
                subs = (
                    STANDARD_SUBS  # 5 sub
                    if code_review_enabled
                    else [s for s in STANDARD_SUBS if s[0] != "review"]  # 4 sub
                )
                for sub_name, label_fn in subs:
                    sections.append({
                        "n": N,
                        "stage": stage_name,
                        "track": track_id,
                        "sub": sub_name,
                        "is_simple": False,
                        "is_affected": True,
                        "label": label_fn(stage_name, test_key),
                        "env": None,
                    })
                    N += 1

    # final-gate (mandatory, always appended)
    sections.append({
        "n": N,
        "stage": "final",
        "track": "final-gate",
        "sub": None,
        "is_simple": False,
        "is_affected": False,
        "label": "最终门控审查",
        "env": None,
    })
    return sections


def format_env_block_quote(env_map: dict[str, str]) -> str:
    """Format the environment block quote for the top of tasks.md.

    Format: > - **environment 选择**：stage1 → env1, stage2 → env2
    """
    if not env_map:
        return ""
    parts = [f"{stage} → {env}" for stage, env in env_map.items()]
    return "> - **environment 选择**：" + "，".join(parts)


def build_on_conditions_comment(section: dict, config: dict,
                                  affected_paths: list[str],
                                  proposal_text: str) -> str:
    """Build HTML comment block for a section heading.

    Documents:
      - stage-level on_conditions evaluations (if section is in a stage with rules)
      - track-level on_conditions evaluations (if section's track has rules)
    """
    stage_cfg = next(
        (s for s in (config.get("stages") or []) if s.get("name") == section["stage"]),
        None
    )
    tracks_cfg = config.get("tracks") or {}
    track_cfg = tracks_cfg.get(section["track"]) or {}

    lines = ["<!-- on_conditions_eval:"]

    stage_rules = (stage_cfg or {}).get("on_conditions") or []
    if stage_rules:
        lines.append(f"     stage={section['stage']}")
        for rule in stage_rules:
            ev = evaluate_on_conditions(rule, affected_paths, proposal_text)
            verdict = "命中" if ev["matched"] else "未命中"
            rationale_parts = []
            if ev["path_hit"]:
                rationale_parts.append("path hit")
            if ev["semantic_hit"]:
                rationale_parts.append("keyword hit")
            rationale = ", ".join(rationale_parts) if rationale_parts else "no hit"
            lines.append(f"     规则: {rule}")
            lines.append(f"       → 机械评估: {verdict} ({rationale})")
    else:
        lines.append(f"     stage={section['stage']} (常驻, 无 on_conditions)")

    if section["track"] and section["track"] != "final-gate":
        track_rules = track_cfg.get("on_conditions") or []
        if track_rules:
            lines.append(f"     track={section['track']}")
            for rule in track_rules:
                ev = evaluate_on_conditions(rule, affected_paths, proposal_text)
                verdict = "命中" if ev["matched"] else "未命中"
                rationale_parts = []
                if ev["path_hit"]:
                    rationale_parts.append("path hit")
                if ev["semantic_hit"]:
                    rationale_parts.append("keyword hit")
                rationale = ", ".join(rationale_parts) if rationale_parts else "no hit"
                lines.append(f"     规则: {rule}")
                lines.append(f"       → 机械评估: {verdict} ({rationale})")
        else:
            lines.append(f"     track={section['track']} (常驻, 无 on_conditions)")

    lines.append("-->")
    return "\n".join(lines)


def format_section_body(section: dict) -> str:
    """Format the body content for a single section."""
    if section["stage"] == "final":
        n = section["n"]
        return (
            f"- [ ] {n}.1 收集所有 stage 的 Gate Assessment\n"
            f"- [ ] {n}.2 检查跨 stage 依赖项\n"
            f"- [ ] {n}.3 输出 Final Gate Assessment"
        )
    if section["is_simple"]:
        n = section["n"]
        track = section["track"]
        return (
            f"- [ ] {n}.1 执行 tracks.{track}.commands"
            f"（runner 派遣 pg-build/simple agent 按序执行）"
        )
    if not section["is_affected"]:
        return "- 无"
    if section["sub"] == "gate":
        return "- 无"
    if section["sub"] == "test":
        return f"- [ ] {section['n']}.1 编写 {section['stage']} 测试：待 LLM 填充"
    if section["sub"] == "dev":
        return f"- [ ] {section['n']}.1 实现功能：待 LLM 填充"
    if section["sub"] == "review":
        # v3.x: review 阶段 placeholder（runner 不展开命令，agent 自己读 profile 配置）
        return (
            f"- [ ] {section['n']}.1 review agent 读 design.md + tasks.md + .pg/code-review/code-review.yaml 细则\n"
            f"- [ ] {section['n']}.2 review agent 对 git diff feat/pg/{{change}} 做静态审查\n"
            f"- [ ] {section['n']}.3 review agent 输出 review_score + p0_failures 到 2-build/{{seq}}-{section['track']}-review.md\n"
            f"- [ ] {section['n']}.4 score < pass_threshold → escalate 至 fix-review；score < escalate_threshold → workflow_failed"
        )
    if section["sub"] == "verify":
        return f"- [ ] {section['n']}.1 执行 lint（runner 通过 modules 注入命令）\n" \
               f"- [ ] {section['n']}.2 执行测试（runner 通过 modules 注入命令）\n" \
               f"- [ ] {section['n']}.3 启动服务（如需）\n" \
               f"- [ ] {section['n']}.4 验证 V-{section['track']}-N：来自 design.md（N 由 design.md 决定，非章节号）"
    return "- 无"


def format_section_evidence_block(section: dict) -> str:
    """Evidence Block placeholder for verify sections."""
    if section["sub"] != "verify":
        return ""
    return (
        "\n  **Evidence 要求**（verify agent 在验证报告中产出，gate agent 据此评审）：\n"
        "  - 每个 V-* 必须有对应的原始输出（curl 响应 / 命令行输出 / 日志片段）\n"
        "  - SKIP 的 V-* 必须注明豁免理由\n"
        "  - 测试结果（Tests run: N, Failures: 0, Errors: 0）必须有日志摘要"
    )


def build_tasks_md(sections: list[dict], env_map: dict[str, str],
                   config: dict, affected_paths: list[str],
                   proposal_text: str) -> str:
    """Generate the full tasks.md skeleton content."""
    out_lines = []

    env_quote = format_env_block_quote(env_map)
    if env_quote:
        out_lines.append(env_quote)
        out_lines.append("")

    for sec in sections:
        if sec["is_simple"]:
            heading = f"## {sec['n']}. {sec['stage']}.{sec['track']} - {sec['label']}"
        elif sec["stage"] == "final":
            heading = f"## {sec['n']}. final-gate - {sec['label']}"
        else:
            heading = f"## {sec['n']}. {sec['stage']}.{sec['track']}:{sec['sub']} - {sec['label']}"
        out_lines.append(heading)
        out_lines.append("")

        comment = build_on_conditions_comment(sec, config, affected_paths, proposal_text)
        out_lines.append(comment)
        out_lines.append("")

        out_lines.append(format_section_body(sec))
        out_lines.append("")

    return "\n".join(out_lines).rstrip() + "\n"


# ============================================================
# on-conditions-eval.md (review stage helper)
# ============================================================

def build_on_conditions_eval_md(config: dict, affected_paths: list[str],
                                 proposal_text: str) -> str:
    """Generate on-conditions-eval.md content (for stage 3 review)."""
    lines = [
        "# on_conditions 评估记录",
        "",
        "> 本文件由 `pg-gen-tasks-skeleton.py` 自动生成。",
        "> LLM 在 review 阶段对每条规则的「机械评估」进行复核，给出最终决策 + 依据。",
        "> 复核完成后，把「最终决策」列同步到 review-notes.md 的「on_conditions 评估记录」段。",
        "",
        "**机械评估列说明**：",
        "- `path`：基于 affected_paths 的 glob 匹配（来自 proposal.md 提取）",
        "- `semantic`：基于 proposal.md 全文的关键词匹配",
        "- `建议`：path 或 semantic 任一命中 → 命中",
        "",
        "## stage 级",
        "",
    ]

    for stage in (config.get("stages") or []):
        rules = stage.get("on_conditions") or []
        if not rules:
            continue
        lines.append(f"### {stage['name']}")
        lines.append("")
        lines.append("| # | 规则 | 机械评估 (path) | 机械评估 (semantic) | 建议 | 最终决策 | 依据 |")
        lines.append("|---|------|----------------|--------------------|------|----------|------|")
        for i, rule in enumerate(rules, 1):
            ev = evaluate_on_conditions(rule, affected_paths, proposal_text)
            path_cell = "✅" if ev["path_hit"] else "❌"
            sem_cell = "✅" if ev["semantic_hit"] else "❌"
            verdict = "命中" if ev["matched"] else "未命中"
            lines.append(f"| {i} | {rule} | {path_cell} | {sem_cell} | {verdict} | [ ] |  |")
        lines.append(f"| **结论** | | | | | [ ] |  |")
        lines.append("")

    lines.append("## track 级")
    lines.append("")

    for track_id, track_cfg in (config.get("tracks") or {}).items():
        rules = track_cfg.get("on_conditions") or []
        if not rules:
            continue
        lines.append(f"### {track_id}")
        lines.append("")
        lines.append("| # | 规则 | 机械评估 (path) | 机械评估 (semantic) | 建议 | 最终决策 | 依据 |")
        lines.append("|---|------|----------------|--------------------|------|----------|------|")
        for i, rule in enumerate(rules, 1):
            ev = evaluate_on_conditions(rule, affected_paths, proposal_text)
            path_cell = "✅" if ev["path_hit"] else "❌"
            sem_cell = "✅" if ev["semantic_hit"] else "❌"
            verdict = "命中" if ev["matched"] else "未命中"
            lines.append(f"| {i} | {rule} | {path_cell} | {sem_cell} | {verdict} | [ ] |  |")
        lines.append(f"| **结论** | | | | | [ ] |  |")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("**LLM review 操作指引**：")
    lines.append("")
    lines.append("1. 对每行「最终决策」勾选 `[x]`（同意机械评估）或 `[~]` + 写「依据」（覆盖机械评估）")
    lines.append("2. 复核完成后，把本文件表格内容**合并到** `.pg/changes/<change>/1-propose-review/review-notes.md` 的「on_conditions 评估记录」段")
    lines.append("3. 合并后本文件可保留作为审计副本")

    return "\n".join(lines) + "\n"


# ============================================================
# Main
# ============================================================

def main():
    args = parse_args()

    try:
        env_map = parse_env_map(args.environment)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    affected_tracks = {t.strip() for t in args.affected_tracks.split(",") if t.strip()}
    selected_stages = {s.strip() for s in args.selected_stages.split(",") if s.strip()}

    try:
        config = load_config()
    except Exception as e:
        print(f"ERROR: 加载 {CONFIG_PATH} 失败: {e}", file=sys.stderr)
        sys.exit(1)

    proposal_text = read_proposal_text(args.proposal_md)
    affected_paths = extract_globs_from_proposal(proposal_text)

    sections = build_sections(config, affected_tracks, selected_stages)

    output_tasks = args.output_tasks or os.path.join(
        CHANGES_DIR, args.change, "tasks.md"
    )
    output_eval = args.output_eval or os.path.join(
        CHANGES_DIR, args.change, "1-propose-review", "on-conditions-eval.md"
    )

    tasks_content = build_tasks_md(
        sections, env_map, config, affected_paths, proposal_text
    )
    eval_content = build_on_conditions_eval_md(
        config, affected_paths, proposal_text
    )

    os.makedirs(os.path.dirname(output_tasks), exist_ok=True)
    os.makedirs(os.path.dirname(output_eval), exist_ok=True)
    with open(output_tasks, "w", encoding="utf-8") as f:
        f.write(tasks_content)
    with open(output_eval, "w", encoding="utf-8") as f:
        f.write(eval_content)

    result = {
        "tasks_md_written": output_tasks,
        "on_conditions_eval_written": output_eval,
        "section_count": len(sections),
        "sections": [
            {
                "n": s["n"],
                "stage": s["stage"],
                "track": s["track"],
                "sub": s["sub"],
                "is_simple": s["is_simple"],
                "is_affected": s["is_affected"],
                "label": s["label"],
            }
            for s in sections
        ],
        "environment_block_quote": format_env_block_quote(env_map),
        "affected_paths": affected_paths,
        "affected_tracks": sorted(affected_tracks),
        "environment_map": env_map,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()