#!/usr/bin/env python3
"""pg-validate-proposal.py — Validate proposal artifacts for pipeline consumption.

Subcommands:
    manifest <change>  — Validate execution-manifest.yaml ↔ tasks.md consistency

Usage:
    python3 pg-validate-proposal.py manifest <change>

Exit code: 0 = valid, 1 = invalid (with error messages to stderr).
"""

import json
import os
import sys
import traceback

try:
    import yaml
except ImportError:
    yaml = None

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

from pg_pipeline_common import (
    CHANGES_DIR,
    CONFIG_PATH,
    PROJECT_ROOT,
    get_track_type,
    load_config,
    parse_tasks_sections,
)

MANIFEST_SCHEMA_PATH = os.path.join(_SCRIPT_DIR, "manifest.schema.json")


def _load_json_schema(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _validate_manifest_against_schema(manifest, schema):
    """Basic JSON Schema validation (no external lib dependency).

    Returns list of (code, message) tuples.
    """
    issues = []
    s = schema

    # Type check root
    if not isinstance(manifest, dict):
        return [("manifest_not_object", "manifest 顶层必须是 object")]

    # Check required fields
    for field in s.get("required", []):
        if field not in manifest:
            issues.append((f"manifest_missing_{field}", f"缺少必填字段: {field}"))

    # schema_version
    if "schema_version" in manifest:
        allowed = s.get("properties", {}).get("schema_version", {}).get("enum", [])
        if manifest["schema_version"] not in allowed:
            issues.append((
                "manifest_schema_version_mismatch",
                f"不支持的 schema_version: {manifest.get('schema_version')!r}, "
                f"允许: {allowed}"
            ))

    # change
    if "change" in manifest and not isinstance(manifest["change"], str):
        issues.append(("manifest_change_not_string", "change 必须是字符串"))

    # stages
    if "stages" in manifest:
        if not isinstance(manifest["stages"], list):
            issues.append(("manifest_stages_not_array", "stages 必须是数组"))
        else:
            for i, stage in enumerate(manifest["stages"]):
                stage_issues = _validate_stage(stage, i)
                issues.extend(stage_issues)

    # final_gate
    if "final_gate" in manifest:
        fg = manifest["final_gate"]
        if not isinstance(fg, dict):
            issues.append(("manifest_final_gate_not_object", "final_gate 必须是 object"))
        elif "tasks_md_section" not in fg:
            issues.append(("manifest_final_gate_missing_section", "final_gate 缺少 tasks_md_section"))

    return issues


def _validate_stage(stage, index):
    issues = []
    prefix = f"stages[{index}]"

    if not isinstance(stage, dict):
        issues.append((f"{prefix}_not_object", f"{prefix} 必须是 object"))
        return issues

    if "name" not in stage:
        issues.append((f"{prefix}_missing_name", f"{prefix} 缺少 name"))
    elif not isinstance(stage["name"], str) or not stage["name"].strip():
        issues.append((f"{prefix}_invalid_name", f"{prefix} name 必须是非空字符串"))

    if "environment" not in stage:
        issues.append((f"{prefix}_missing_environment", f"{prefix} 缺少 environment"))
    elif not isinstance(stage["environment"], str):
        issues.append((f"{prefix}_environment_not_string", f"{prefix} environment 必须是字符串"))

    if "tracks" not in stage:
        issues.append((f"{prefix}_missing_tracks", f"{prefix} 缺少 tracks"))
        return issues

    if not isinstance(stage["tracks"], list):
        issues.append((f"{prefix}_tracks_not_array", f"{prefix} tracks 必须是数组"))
        return issues

    for j, track in enumerate(stage["tracks"]):
        track_prefix = f"{prefix}.tracks[{j}]"
        track_issues = _validate_track(track, track_prefix)
        issues.extend(track_issues)

    return issues


def _validate_track(track, prefix):
    issues = []

    if not isinstance(track, dict):
        issues.append((f"{prefix}_not_object", f"{prefix} 必须是 object"))
        return issues

    if "id" not in track:
        issues.append((f"{prefix}_missing_id", f"{prefix} 缺少 id"))
    elif not isinstance(track["id"], str) or not track["id"].strip():
        issues.append((f"{prefix}_invalid_id", f"{prefix} id 必须是非空字符串"))

    # v3: enabled 字段必填（pg-build 派发唯一依据）
    if "enabled" not in track:
        issues.append((f"{prefix}_missing_enabled",
                       f"{prefix} 缺少 enabled 字段（v3 必填，pg-build 派发依据）"))
    elif not isinstance(track["enabled"], bool):
        issues.append((f"{prefix}_invalid_enabled",
                       f"{prefix} enabled 必须是 bool, 实际: {type(track['enabled']).__name__}"))

    # v3: e2e track 必填 target_module
    if track.get("type") == "e2e" and not track.get("target_module"):
        issues.append((f"{prefix}_e2e_missing_target_module",
                       f"{prefix} type=e2e 必须包含 target_module"))

    track_type = track.get("type")
    VALID_TYPES = ("standard", "simple", "scenario")
    if track_type not in VALID_TYPES:
        issues.append((f"{prefix}_invalid_type",
                       f"{prefix} type 必须是 'standard', 'simple' 或 'scenario', 实际: {track_type!r}"))
    if track_type == "standard":
        if "phase_prompts" not in track:
            issues.append((f"{prefix}_missing_phase_prompts",
                           f"{prefix} type=standard 必须包含 phase_prompts"))
        else:
            pp = track["phase_prompts"]
            if not isinstance(pp, dict):
                issues.append((f"{prefix}_phase_prompts_not_object",
                               f"{prefix} phase_prompts 必须是 object"))
            else:
                # v3.4: test / dev 强必填；review/verify/gate 都 optional
                for required_sub in ("test", "dev"):
                    if required_sub not in pp:
                        issues.append((f"{prefix}_missing_sub_{required_sub}",
                                       f"{prefix} phase_prompts 缺少 {required_sub}"))
                    elif not isinstance(pp[required_sub], dict) or "tasks_md_section" not in pp[required_sub]:
                        issues.append((f"{prefix}_invalid_sub_{required_sub}",
                                       f"{prefix} phase_prompts.{required_sub} 缺少或无效 tasks_md_section"))
                # review / verify / gate 若存在必须 valid
                for optional_sub in ("review", "verify", "gate"):
                    if optional_sub in pp:
                        if not isinstance(pp[optional_sub], dict) or "tasks_md_section" not in pp[optional_sub]:
                            issues.append((f"{prefix}_invalid_sub_{optional_sub}",
                                           f"{prefix} phase_prompts.{optional_sub} 缺少或无效 tasks_md_section"))
                # 必须保留至少一个质量门：verify 或 gate 二者必有其一
                # （review 单独存在不算质量门——它是静态审查，不替代运行时验证）
                if "verify" not in pp and "gate" not in pp:
                    issues.append((f"{prefix}_no_quality_gate",
                                   f"{prefix} phase_prompts 必须包含 verify 或 gate 至少一项（review 单独存在不算质量门）"))
                # 反向校验：simple track 不应出现 review
                if track_type == "simple" and "review" in pp:
                    issues.append((f"{prefix}_simple_track_with_code_view",
                                   f"{prefix} type=simple 不应包含 review sub"))
        if "commands" in track:
            issues.append((f"{prefix}_unexpected_commands",
                           f"{prefix} type=standard 不应包含 commands 字段"))

    elif track_type == "scenario":
        # v3.5: scenario track 校验规则
        if "phase_prompts" not in track:
            issues.append((f"{prefix}_missing_phase_prompts",
                           f"{prefix} type=scenario 必须包含 phase_prompts"))
        else:
            pp = track["phase_prompts"]
            if not isinstance(pp, dict):
                issues.append((f"{prefix}_phase_prompts_not_object",
                               f"{prefix} phase_prompts 必须是 object"))
            else:
                for required_sub in ("scenario-prepare", "scenario-execute"):
                    if required_sub not in pp:
                        issues.append((f"{prefix}_missing_sub_{required_sub}",
                                       f"{prefix} phase_prompts 缺少 {required_sub}"))
                    elif not isinstance(pp[required_sub], dict) or "tasks_md_section" not in pp[required_sub]:
                        issues.append((f"{prefix}_invalid_sub_{required_sub}",
                                       f"{prefix} phase_prompts.{required_sub} 缺少或无效 tasks_md_section"))
        if "commands" in track:
            issues.append((f"{prefix}_unexpected_commands",
                           f"{prefix} type=scenario 不应包含 commands 字段"))

    if track_type == "simple":
        if "commands" not in track:
            issues.append((f"{prefix}_missing_commands",
                           f"{prefix} type=simple 必须包含 commands"))
        elif not isinstance(track["commands"], list) or len(track["commands"]) == 0:
            issues.append((f"{prefix}_invalid_commands",
                           f"{prefix} commands 必须是非空字符串数组"))
        if "phase_prompts" in track:
            issues.append((f"{prefix}_unexpected_phase_prompts",
                           f"{prefix} type=simple 不应包含 phase_prompts"))

    return issues


def _validate_manifest_vs_tasks(manifest, tasks_sections):
    """Validate manifest section references exist in tasks.md sections."""
    issues = []
    section_keys = {s["section_key"] for s in tasks_sections}

    for stage_idx, stage in enumerate(manifest.get("stages", [])):
        for track_idx, track in enumerate(stage.get("tracks", [])):
            if track.get("type") == "simple":
                continue
# v3.5: scenario track uses scenario-prepare/scenario-execute instead of test/dev
            if track.get("type") == "scenario":
                subs_to_check = ("scenario-prepare", "scenario-execute")
            else:
                subs_to_check = ("test", "dev", "review", "verify", "gate")
            for sub_name in subs_to_check:
                pp = track.get("phase_prompts", {})
                if sub_name not in pp:
                    continue
                ref = pp[sub_name].get("tasks_md_section", "")
                if ref not in section_keys:
                    issues.append((
                        "manifest_section_missing",
                        f"stages[{stage_idx}].tracks[{track_idx}].phase_prompts.{sub_name} "
                        f"引用了不存在的 tasks.md section: {ref!r}"
                    ))

    # Validate final_gate section exists
    fg = manifest.get("final_gate", {})
    fg_ref = fg.get("tasks_md_section", "")
    if fg_ref and fg_ref not in section_keys:
        issues.append((
            "manifest_final_gate_section_missing",
            f"final_gate 引用了不存在的 tasks.md section: {fg_ref!r}"
        ))

    return issues


def _validate_tracks_against_tasks(manifest, tasks_sections, config):
    """Validate track types in manifest match project.yaml config."""
    issues = []

    for stage in manifest.get("stages", []):
        for track in stage.get("tracks", []):
            track_id = track.get("id", "")
            expected_type = get_track_type(config, track_id)
            manifest_type = track.get("type")

            # v3: e2e / scenario track 在 pg_pipeline_common 中归类为 "track"，
            # manifest 端需匹配其显式 type
            track_cfg = (config.get("tracks") or {}).get(track_id, {})
            explicit_type = track_cfg.get("type")
            if explicit_type in ("e2e", "scenario"):
                if manifest_type != explicit_type:
                    issues.append((
                        "manifest_track_type_mismatch",
                        f"track {track_id!r} 在 project.yaml 中是 {explicit_type} 类型，"
                        f"但 manifest 中标记为 {manifest_type!r}"
                    ))
                continue

            if expected_type == "phase" and manifest_type != "simple":
                issues.append((
                    "manifest_track_type_mismatch",
                    f"track {track_id!r} 在 project.yaml 中是 simple 类型，"
                    f"但 manifest 中标记为 {manifest_type!r}"
                ))
            if expected_type == "track" and manifest_type not in ("standard", "e2e", "scenario"):
                issues.append((
                    "manifest_track_type_mismatch",
                    f"track {track_id!r} 在 project.yaml 中是 standard 类型，"
                    f"但 manifest 中标记为 {manifest_type!r}"
                ))
            # v3.5: scenario type validation
            if expected_type == "scenario" and manifest_type != "scenario":
                issues.append((
                    "manifest_track_type_mismatch",
                    f"track {track_id!r} 在 project.yaml 中是 scenario 类型，"
                    f"但 manifest 中标记为 {manifest_type!r}"
                ))

    return issues


def _validate_environment(manifest, config):
    """Validate all referenced environments exist in project.yaml."""
    issues = []
    envs = config.get("environments") or {}

    for stage in manifest.get("stages", []):
        env_name = stage.get("environment", "")
        if env_name and env_name not in envs:
            issues.append((
                "manifest_environment_invalid",
                f"stage {stage.get('name', '')!r} 引用的 environment "
                f"{env_name!r} 不在 project.yaml environments 列表中"
            ))

    return issues


def cmd_manifest(change):
    """Validate execution-manifest.yaml consistency."""
    manifest_path = os.path.join(CHANGES_DIR, change, "execution-manifest.yaml")
    tasks_path = os.path.join(CHANGES_DIR, change, "tasks.md")

    all_issues = []
    valid = True

    # 1. Check files exist
    if not os.path.isfile(manifest_path):
        print(f"ERROR: manifest 不存在: {manifest_path}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(tasks_path):
        print(f"ERROR: tasks.md 不存在: {tasks_path}", file=sys.stderr)
        sys.exit(1)

    # 2. Load
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = yaml.safe_load(f)
    except Exception as e:
        print(f"ERROR: manifest 解析失败: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        tasks_sections = parse_tasks_sections(tasks_path)
    except Exception as e:
        print(f"ERROR: tasks.md 解析失败: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        config = load_config()
    except Exception as e:
        print(f"ERROR: project.yaml 加载失败: {e}", file=sys.stderr)
        sys.exit(1)

    # 3. Validate schema (structural)
    schema_path = MANIFEST_SCHEMA_PATH
    try:
        schema = _load_json_schema(schema_path)
    except Exception as e:
        print(f"WARN: schema 加载失败（跳过 schema 校验）: {e}", file=sys.stderr)
        schema = {}

    schema_issues = _validate_manifest_against_schema(manifest, schema)
    for code, msg in schema_issues:
        print(f"  [{code}] {msg}", file=sys.stderr)
        all_issues.append(code)

    # 4. Validate section references vs tasks.md
    cross_issues = _validate_manifest_vs_tasks(manifest, tasks_sections)
    for code, msg in cross_issues:
        print(f"  [{code}] {msg}", file=sys.stderr)
        all_issues.append(code)

    # 5. Validate track types vs project.yaml
    track_type_issues = _validate_tracks_against_tasks(manifest, tasks_sections, config)
    for code, msg in track_type_issues:
        print(f"  [{code}] {msg}", file=sys.stderr)
        all_issues.append(code)

    # 6. Validate environments
    env_issues = _validate_environment(manifest, config)
    for code, msg in env_issues:
        print(f"  [{code}] {msg}", file=sys.stderr)
        all_issues.append(code)

    # 7. v3.5: Validate scenario.yaml exists if manifest has scenario track
    scenario_yaml_path = os.path.join(CHANGES_DIR, change, "scenario.yaml")
    has_scenario = any(
        track.get("type") == "scenario"
        for stage in manifest.get("stages", [])
        for track in stage.get("tracks", [])
    )
    if has_scenario and not os.path.isfile(scenario_yaml_path):
        code = "scenario_yaml_missing"
        msg = (f"manifest 包含 type=scenario 的 track 但 scenario.yaml 不存在: "
               f"{scenario_yaml_path}")
        print(f"  [{code}] {msg}", file=sys.stderr)
        all_issues.append(code)

    # 7. Result
    if all_issues:
        valid = False
        print(f"\nFAILED: {len(all_issues)} issue(s) found", file=sys.stderr)
    else:
        print("OK: all manifest checks passed")

    sys.exit(0 if valid else 1)


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 pg-validate-proposal.py manifest <change>", file=sys.stderr)
        sys.exit(1)

    subcmd = sys.argv[1]
    change = sys.argv[2]

    if subcmd == "manifest":
        cmd_manifest(change)
    else:
        print(f"未知子命令: {subcmd}", file=sys.stderr)
        print("支持: manifest", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
