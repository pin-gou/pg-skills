#!/usr/bin/env python3
"""pg-gen-manifest.py — Generate execution-manifest.yaml from tasks.md + project.yaml.

Usage:
    python3 pg-gen-manifest.py <change>

Reads tasks.md and project.yaml, then generates .pg/changes/<change>/execution-manifest.yaml
that encodes which stages/tracks to run, their environment, and which tasks.md sections
to use as prompt source for each sub-phase.

This is a pure-function CLI: zero side effects beyond writing the manifest file.
"""

import os
import re
import sys

try:
    import yaml
except ImportError:
    yaml = None

# Add pg-build scripts dir to path for imports
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

from pg_pipeline_common import (
    PROJECT_ROOT,
    CHANGES_DIR,
    CONFIG_PATH,
    load_config,
    parse_tasks_sections,
    get_track_type,
    count_tasks,
)


# ============================================================
# on_conditions mechanical evaluation (v3 manifest 增强)
# ============================================================

def _extract_globs_from_rule(rule: str) -> list[str]:
    """从 on_conditions 自然语言规则中提取 glob 模式."""
    globs = []
    for tok in re.split(r"[\s,，。;；\"'`]+", rule):
        if "/" in tok or tok.endswith("**") or tok.startswith("**"):
            globs.append(tok)
    return globs


def _check_glob_match(rule: str, affected_paths: list[str]) -> bool:
    """检查规则的 glob 模式是否匹配任意 affected_path."""
    globs = _extract_globs_from_rule(rule)
    if not globs or not affected_paths:
        return False
    import fnmatch
    for glob in globs:
        for path in affected_paths:
            if fnmatch.fnmatch(path, glob) or fnmatch.fnmatch(path, glob.rstrip("/") + "/**"):
                return True
            if glob.endswith("/**") and path.startswith(glob[:-3]):
                return True
            if glob in path:
                return True
    return False


def _check_keyword_match(rule: str, proposal_text: str) -> bool:
    """检查规则的关键词是否出现在 proposal.md 中."""
    if not proposal_text:
        return False
    stop_phrases = {
        "本变更", "本stage", "本track", "任一", "包含", "描述",
        "修改", "新增", "涉及", "命中", "是否", "以下", "情况", "当",
        "时", "则", "的", "了", "在", "和", "与", "或", "为", "是",
        "对", "一个", "所有", "每个", "以", "由", "被", "可",
        "打开", "关闭", "启用", "禁用", "忽略", "执行", "支持",
        "激活", "写入", "设置", "调整", "增加", "减少", "改动",
    }
    keywords = []
    for tok in re.split(r"[\s,，。;；\"'`()()【】\[\]/\\*]+", rule):
        if not tok or len(tok) < 2 or tok in stop_phrases:
            continue
        if tok.isascii() and not re.search(r"[a-zA-Z]", tok):
            continue
        keywords.append(tok)
    for kw in keywords:
        if kw in proposal_text:
            return True
    return False


def _evaluate_on_conditions(
    rules: list[str], affected_paths: list[str], proposal_text: str,
) -> dict:
    """机械评估 track.<id>.on_conditions 列表.

    Returns:
        {
          "matched_rules": [rule1, ...],
          "unmatched_rules": [rule4, ...],
          "path_hit_count": int,
          "semantic_hit_count": int,
        }
    """
    matched = []
    unmatched = []
    path_hits = 0
    sem_hits = 0
    for rule in rules:
        path_hit = _check_glob_match(rule, affected_paths)
        sem_hit = _check_keyword_match(rule, proposal_text)
        if path_hit or sem_hit:
            matched.append(rule)
            if path_hit:
                path_hits += 1
            if sem_hit:
                sem_hits += 1
        else:
            unmatched.append(rule)
    return {
        "matched_rules": matched,
        "unmatched_rules": unmatched,
        "path_hit_count": path_hits,
        "semantic_hit_count": sem_hits,
    }


def _extract_affected_paths_from_proposal(change: str) -> list[str]:
    """从 proposal.md 提取 affected_paths（glob 列表）."""
    proposal_path = os.path.join(CHANGES_DIR, change, "proposal.md")
    if not os.path.isfile(proposal_path):
        return []
    try:
        with open(proposal_path, encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return []
    seen = set()
    out = []
    for m in re.finditer(r"`([^`\n]+)`", text):
        body = m.group(1).strip()
        if "/" in body or "*" in body:
            if body not in seen:
                seen.add(body)
                out.append(body)
    for m in re.finditer(r"\*\*[^*\n]+\*\*\s*[:：]\s*([^\n]+)", text):
        body = m.group(1).strip().strip("`").rstrip(",，").rstrip(".")
        if "/" in body or "*" in body:
            if body not in seen:
                seen.add(body)
                out.append(body)
    return out


def _build_track_enabled_decision(
    track_id: str,
    track_cfg: dict,
    eval_dict: dict,
    in_affected_tracks: bool,
) -> tuple[bool, str]:
    """根据 LLM 决策 + 机械评估确定 track 是否启用，并构造 reason 字符串.

    决策优先级:
      1. 若 track 无 on_conditions 字段 → 视为常驻，遵循 LLM 决策
      2. 若 on_conditions 全部未命中 → LLM 决策结果直接采纳
      3. 若 on_conditions 全部命中 → LLM 决策结果直接采纳
      4. 若 on_conditions 部分命中 → 机械评估为"建议启用"，采纳 LLM 决策

    Returns:
        (enabled, reason)
    """
    rules = track_cfg.get("on_conditions") or []
    if not rules:
        # 无 on_conditions → 常驻
        if in_affected_tracks:
            return (True, "常驻 track（无 on_conditions），LLM 决策启用")
        else:
            return (False, "常驻 track（无 on_conditions），但 LLM 未将本 track 列入 affected_tracks")

    matched = eval_dict.get("matched_rules", [])
    unmatched = eval_dict.get("unmatched_rules", [])

    if matched and not unmatched:
        reason = f"on_conditions 全部命中（{len(matched)} 条），LLM 决策启用"
        return (True, reason)
    if unmatched and not matched:
        reason = f"on_conditions 全部未命中（{len(unmatched)} 条），LLM 决策{'启用' if in_affected_tracks else '禁用'}"
        return (in_affected_tracks, reason)
    # 部分命中
    if in_affected_tracks:
        reason = (
            f"on_conditions 部分命中（{len(matched)}/{len(rules)}），"
            f"LLM 决策启用"
        )
        return (True, reason)
    else:
        reason = (
            f"on_conditions 部分命中（{len(matched)}/{len(rules)}），"
            f"但 LLM 未将本 track 列入 affected_tracks → 建议人工复核"
        )
        return (False, reason)


def _parse_stage_env_from_tasks_md(content: str) -> dict[str, str]:
    """Parse environment mapping from tasks.md top block quote.

    Expected format (in the top block quote):
        > - **environment 选择**：dev → dev-local

    Returns dict mapping stage-name to environment-name (e.g. {"dev": "dev-local"}).
    """
    result = {}
    for line in content.splitlines():
        line = line.strip()
        m = re.match(r'>\s*-\s*\*\*environment\s*选择\*\*\s*[:：]\s*(.+?)(?:\s*（|$)', line)
        if not m:
            continue
        for part in m.group(1).split('，'):
            part = part.strip().rstrip('）')
            cm = re.match(r'(\w+)\s*[→➜]\s*(\S+)', part)
            if cm:
                result[cm.group(1)] = cm.group(2)
    return result


_SECTION_KEY_RE = re.compile(
    r'^\d+\.\s+(?P<stage>[\w-]+)\.(?P<track>[\w-]+)'
    r'(?::(?P<sub>[\w-]+))?\s*-\s*(?P<label>.+)$'
)


def _parse_section_key(section_key: str) -> dict | None:
    """Parse a section_key like '1. dev.backend:test - label' into structured fields.

    Returns dict with keys: stage, track, sub (or None), label
    Returns None if parsing fails (not a standard section format).
    """
    m = _SECTION_KEY_RE.match(section_key)
    if not m:
        return None
    return {
        "stage": m.group("stage"),
        "track": m.group("track"),
        "sub": m.group("sub"),
        "label": m.group("label"),
    }


def build_manifest(change: str) -> dict:
    """Build the execution manifest dict for the given change."""
    config = load_config()
    tasks_path = os.path.join(CHANGES_DIR, change, "tasks.md")

    with open(tasks_path, encoding="utf-8") as f:
        tasks_content = f.read()

    sections = parse_tasks_sections(tasks_path)

    # Extract environment mapping from tasks.md top block quote
    env_map = _parse_stage_env_from_tasks_md(tasks_content)
    tracks_cfg = config.get("tracks") or {}

    # v3: 准备 on_conditions 机械评估所需的输入
    affected_paths = _extract_affected_paths_from_proposal(change)
    proposal_path = os.path.join(CHANGES_DIR, change, "proposal.md")
    proposal_text = ""
    if os.path.isfile(proposal_path):
        try:
            with open(proposal_path, encoding="utf-8") as f:
                proposal_text = f.read()
        except Exception:
            proposal_text = ""

    # 解析 LLM 在 stage 2c 决策的 affected_tracks（来自 tasks.md 实际生成的 track 列表）
    affected_tracks_set = set()
    for sec in sections:
        parsed = _parse_section_key(sec["section_key"])
        if parsed is not None:
            affected_tracks_set.add(parsed["track"])

    # Build per-stage, per-track structure from sections
    # stage_tracks: {stage_name: {track_id: {phases: {sub: section_key}, is_simple: bool}}}
    stage_tracks: dict[str, dict] = {}
    final_gate_section = None

    for sec in sections:
        parsed = _parse_section_key(sec["section_key"])
        if parsed is None:
            # Check if this is a final-gate section
            if "final-gate" in sec["section_key"] or "final_gate" in sec["section_key"]:
                final_gate_section = sec["section_key"]
                continue
            continue

        stage = parsed["stage"]
        track = parsed["track"]
        sub = parsed["sub"]

        if stage not in stage_tracks:
            stage_tracks[stage] = {}

        if track not in stage_tracks[stage]:
            raw_type = get_track_type(config, track)
            track_cfg = config.get("tracks", {}).get(track, {})
            # v3.5: scenario 类型保持为 "scenario"，不 fallback to standard/simple
            if raw_type == "scenario":
                manifest_type = "scenario"
            elif raw_type == "track":
                manifest_type = "standard"
            else:
                manifest_type = "simple"
            stage_tracks[stage][track] = {
                "phases": {},
                "all_noop": True,
                "type": manifest_type,
                "track_cfg": track_cfg,
            }

        stage_tracks[stage][track]["phases"][sub] = sec["section_key"]

        # Check if this section body is noop
        _, _, all_noop = count_tasks(sec["body"].splitlines(keepends=True))
        if not all_noop:
            stage_tracks[stage][track]["all_noop"] = False

    # Build manifest stages
    manifest_stages = []
    for stage_name, tracks_dict in stage_tracks.items():
        manifest_tracks = []
        for track_id, track_info in tracks_dict.items():
            # v3.5: scenario track 即使 all_noop 也不跳过（常驻节点，必出现在 manifest 中）
            is_scenario = (track_info["type"] == "scenario")
            if track_info["all_noop"] and not is_scenario:
                continue

            # v3: 计算 enabled / reason / on_conditions_eval / target_module
            track_cfg = track_info["track_cfg"]
            rules = track_cfg.get("on_conditions") or []
            eval_dict = _evaluate_on_conditions(rules, affected_paths, proposal_text)
            in_affected = track_id in affected_tracks_set
            enabled, reason = _build_track_enabled_decision(
                track_id, track_cfg, eval_dict, in_affected,
            )

            entry = {
                "id": track_id,
                "type": track_info["type"],
                "enabled": enabled,
                "reason": reason,
                "on_conditions_eval": eval_dict,
            }

            # e2e track: 强制要求 target_module
            if track_info["type"] == "e2e":
                tm = track_cfg.get("target_module", "")
                entry["target_module"] = tm

            if track_info["type"] == "simple":
                # Read commands from project.yaml
                raw_commands = track_cfg.get("commands", [])
                commands = []
                for cmd in raw_commands:
                    if isinstance(cmd, str):
                        commands.append(cmd)
                    elif isinstance(cmd, dict):
                        commands.append(cmd.get("cmd", ""))
                entry["commands"] = commands
            elif is_scenario:
                # v3.5: scenario track phase_prompts
                prompts = {}
                scenario_sub_order = ("scenario-prepare", "scenario-execute")
                for sub_name in scenario_sub_order:
                    if sub_name in track_info["phases"]:
                        prompts[sub_name] = {
                            "tasks_md_section": track_info["phases"][sub_name]
                        }
                if prompts:
                    entry["phase_prompts"] = prompts
            else:
                # Standard / e2e / scenario track: include phase_prompts
                # v3.x: tasks.md 实际生成的 sub 列表决定 phase_prompts
                # 不再硬编码 4/5 sub，按 tasks.md sections parse 出来的内容动态决定
                # v3: 增加 e2e / scenario 子阶段（Phase 4a/4b 启用）
                # v3.5 兼容性：保留 verify（Phase 2 才会移除）
                prompts = {}
                # 按 phase 顺序遍历，确保 manifest 顺序稳定
                sub_order = ("test", "dev", "review", "verify", "e2e", "scenario", "gate")
                for sub_name in sub_order:
                    if sub_name in track_info["phases"]:
                        prompts[sub_name] = {
                            "tasks_md_section": track_info["phases"][sub_name]
                        }
                if prompts:
                    entry["phase_prompts"] = prompts

            manifest_tracks.append(entry)

        if not manifest_tracks:
            continue

        stage_entry = {
            "name": stage_name,
            "environment": env_map.get(stage_name, "dev-local"),
            "tracks": manifest_tracks,
        }
        manifest_stages.append(stage_entry)

    manifest = {
        "schema_version": "2026-06-30",
        "change": change,
        "stages": manifest_stages,
    }

    if final_gate_section:
        manifest["final_gate"] = {
            "tasks_md_section": final_gate_section,
        }

    return manifest


def _resolve_manifest_track_type(raw_type: str, track_cfg: dict) -> str:
    """将 pg_pipeline_common 的 raw_type 映射到 manifest.type 枚举.

    pg_pipeline_common 返回:
      - "track" (standard)
      - "phase" (simple)

    manifest.type 支持:
      - "standard" / "simple" / "e2e" / "scenario"

    当 track_cfg.type 显式声明为 e2e / scenario 时优先采用。
    """
    explicit = track_cfg.get("type")
    if explicit in ("e2e", "scenario"):
        return explicit
    if explicit == "simple" or raw_type == "phase":
        return "simple"
    return "standard"


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 pg-gen-manifest.py <change>", file=sys.stderr)
        sys.exit(1)

    change = sys.argv[1]
    manifest = build_manifest(change)

    output_path = os.path.join(CHANGES_DIR, change, "execution-manifest.yaml")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(manifest, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    print(f"manifest written: {output_path}")
    print(f"  stages: {len(manifest['stages'])}")
    total_tracks = sum(len(s["tracks"]) for s in manifest["stages"])
    print(f"  tracks: {total_tracks}")
    if "final_gate" in manifest:
        print(f"  final-gate: {manifest['final_gate']['tasks_md_section']}")


if __name__ == "__main__":
    main()
