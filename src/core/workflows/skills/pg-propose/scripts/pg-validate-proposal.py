#!/usr/bin/env python3
"""pg-validate-proposal.py — Validate proposal artifacts for pipeline consumption.

Subcommands:
    manifest <change>        — Validate execution-manifest.yaml ↔ tasks.md consistency
    define-summary <change>  — Validate 0-define/define-summary.yaml (pg-1-define 产物, 阶段 1.8)

Usage:
    python3 pg-validate-proposal.py manifest <change>
    python3 pg-validate-proposal.py define-summary <change>

Exit code: 0 = valid, 1 = invalid (with error messages to stderr).
"""
from __future__ import annotations

import json
import os
import re
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

# v3.7: 加载 pg-gen-scenario.py 以获取 placeholder 校验函数
_PG_GEN_SCENARIO_PATH = os.path.join(_SCRIPT_DIR, "pg-gen-scenario.py")
_spec = None
try:
    import importlib.util as _importlib_util
    _spec = _importlib_util.spec_from_file_location("pg_gen_scenario_validator", _PG_GEN_SCENARIO_PATH)
    if _spec is not None and _spec.loader is not None:
        _pg_gen_scenario = _importlib_util.module_from_spec(_spec)
        _spec.loader.exec_module(_pg_gen_scenario)
    else:
        _pg_gen_scenario = None
except Exception as _e:
    print(f"WARN: pg-gen-scenario.py 加载失败（跳过 placeholder 校验）: {_e}", file=sys.stderr)
    _pg_gen_scenario = None


MANIFEST_SCHEMA_PATH = os.path.join(_SCRIPT_DIR, "manifest.schema.json")


# v1.1.0: scenario given 硬编码 endpoint 校验规则 (P0-1)
# 黑名单 + 白名单前缀 + 环境开关
_HARDCODED_IPV4_RE = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
_HARDCODED_SSH_USER_RE = re.compile(r"\bssh://[\w.-]+@")
_HARDCODED_HTTP_PORT_RE = re.compile(r"\bhttps?://[\w.-]+:\d{2,5}\b")
_HARDCODED_PORT_LITERAL_RE = re.compile(r"\bport\s*[=:]?\s*\d{4,5}\b", re.IGNORECASE)
# 本地开发常用豁免
_HARDCODED_LOCAL_ALLOWLIST = {"127.0.0.1", "localhost", "0.0.0.0"}
# 占位符 / 注释 / URL scheme 前缀
_HARDCODED_ALLOW_PREFIXES = ("{env.", "#", "//", "https://{env.", "http://{env.")

# v1.3: env_resource_refs 强引用 — design.md / scenario-*.yaml 至少引用一次
_ENV_REF_PATTERN = re.compile(
    r"\{env\.(infra_services|business_systems|data_resources|config_resources|"
    r"runtime_environment|external_dependencies)\[([^\]]+)\]"
)

# v1.1.0: 通过环境变量可临时关闭新规则 (回滚路径)
def _hardcoded_rule_enabled() -> bool:
    """lazy 读取环境变量, 允许测试在 import 后切换开关."""
    return os.environ.get("PG_PROPOSE_V110_HARDCODED", "1") != "0"


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
                for required_sub in ("scenario-execute",):
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
# v3.x: scenario track uses scenario-execute only (scenario-prepare removed,
            # env readiness handled by restart_all_instances env-action)
            if track.get("type") == "scenario":
                subs_to_check = ("scenario-execute",)
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


def _validate_three_product_consistency(manifest, change_or_path) -> list[tuple[str, str]]:
    """v3.6: tasks.md / manifest / scenario-<track>.yaml 三产物与 SSOT 一致性校验。

    Args:
        manifest: 已解析的 manifest dict
        change_or_path: change 名 (从 CHANGES_DIR 拼) 或 change 根目录绝对路径

    校验规则 (per track):
      - manifest 含 type=scenario track (enabled=true) → scenario-<track>.yaml 必须存在
      - manifest 含 type=scenario track (enabled=false) → scenario-<track>.yaml 必须不存在
      - manifest 不含 type=scenario track → 任何 scenario-*.yaml 必须不存在

    Returns: list of (code, msg)
    """
    issues = []
    if os.path.isabs(change_or_path):
        change_root = change_or_path
    else:
        change_root = os.path.join(CHANGES_DIR, change_or_path)

    # 收集所有已存在的 scenario-<track>.yaml 文件
    import glob as glob_mod
    existing_scenario_files = {
        os.path.basename(f)
        for f in glob_mod.glob(os.path.join(change_root, "scenario-*.yaml"))
    }

    for stage_idx, stage in enumerate(manifest.get("stages", [])):
        for track_idx, track in enumerate(stage.get("tracks", [])):
            if track.get("type") != "scenario":
                continue
            track_id = track.get("id", f"<track-{track_idx}>")
            expected_file = f"scenario-{track_id}.yaml"
            track_enabled = track.get("enabled", False)

            if track_enabled and expected_file not in existing_scenario_files:
                code = "scenario_yaml_missing"
                msg = (f"stages[{stage_idx}].tracks[{track_idx}] 是 enabled=true 的 scenario track "
                       f"({track_id}), 但 {expected_file} 不存在: {os.path.join(change_root, expected_file)}")
                issues.append((code, msg))
            if not track_enabled and expected_file in existing_scenario_files:
                code = "scenario_yaml_should_not_exist"
                msg = (f"stages[{stage_idx}].tracks[{track_idx}] 是 enabled=false 的 scenario track "
                       f"({track_id}), 但 {expected_file} 存在 (会变成冗余产物)")
                issues.append((code, msg))

    # v3.7: placeholder 校验（仅对 enabled=true 的 track 对应的 yaml 文件）
    if _pg_gen_scenario is not None:
        for stage_idx, stage in enumerate(manifest.get("stages", [])):
            for track_idx, track in enumerate(stage.get("tracks", [])):
                if track.get("type") != "scenario":
                    continue
                if not track.get("enabled", False):
                    continue
                track_id = track.get("id", f"<track-{track_idx}>")
                filename = f"scenario-{track_id}.yaml"
                scenario_path = os.path.join(change_root, filename)
                if not os.path.isfile(scenario_path):
                    continue  # 已由 scenario_yaml_missing 报告
                try:
                    placeholder_issues = _pg_gen_scenario.check_scenario_file(scenario_path)
                except Exception as e:
                    placeholder_issues = [(
                        "scenario_placeholder_unfilled",
                        f"{filename} placeholder 校验异常: {e}"
                    )]
                for code, msg in placeholder_issues:
                    prefixed = (
                        f"stages[{stage_idx}].tracks[{track_idx}] "
                        f"({track_id}) → {filename}: {msg}"
                    )
                    issues.append((code, prefixed))

    # 反向: 存在 scenario-*.yaml 但 manifest 无对应 scenario track
    expected_from_manifest = {
        f"scenario-{track['id']}.yaml"
        for stage in manifest.get("stages", [])
        for track in stage.get("tracks", [])
        if track.get("type") == "scenario"
    }
    for fname in existing_scenario_files:
        if fname not in expected_from_manifest:
            code = "scenario_yaml_orphan"
            msg = (f"{fname} 存在但 manifest 不含对应的 type=scenario track: "
                   f"{os.path.join(change_root, fname)}")
            issues.append((code, msg))

    # v1.1.0: scenario given / when / then 硬编码 endpoint 校验 (P0-1)
    if _hardcoded_rule_enabled():
        issues.extend(
            _validate_scenario_no_hardcoded_endpoint(
                change_root, existing_scenario_files, expected_from_manifest,
            )
        )

    return issues


def _validate_scenario_no_hardcoded_endpoint(
    change_root: str,
    existing_scenario_files: set[str],
    expected_from_manifest: set[str],
) -> list[tuple[str, str]]:
    """v1.1.0 (P0-1): 校验 enabled scenario track 的 yaml 不得含硬编码 IP / ssh 用户 / 端口.

    只检查 expected_from_manifest 中出现的文件 (避免孤儿 yaml 二次报错).
    豁免: 注释行 / 占位符前缀 / 本地开发 IP.
    """
    if not _hardcoded_rule_enabled():
        return []

    issues: list[tuple[str, str]] = []
    if yaml is None:
        return issues

    patterns = (
        ("ipv4", _HARDCODED_IPV4_RE),
        ("ssh_user", _HARDCODED_SSH_USER_RE),
        ("http_endpoint", _HARDCODED_HTTP_PORT_RE),
        ("port_literal", _HARDCODED_PORT_LITERAL_RE),
    )

    for filename in sorted(existing_scenario_files & expected_from_manifest):
        scenario_path = os.path.join(change_root, filename)
        try:
            with open(scenario_path, encoding="utf-8") as f:
                doc = yaml.safe_load(f)
        except Exception as e:
            issues.append((
                "scenario_yaml_invalid",
                f"{filename}: YAML 解析失败, 跳过硬编码校验: {e}",
            ))
            continue
        if not isinstance(doc, dict):
            continue

        scenarios = doc.get("scenarios") or []
        for s_idx, scenario in enumerate(scenarios):
            if not isinstance(scenario, dict):
                continue
            scenario_id = scenario.get("scenario_id", f"<scenario-{s_idx}>")

            # 扫描的字段: given (list), when (list of dict), then (list), evidence (list)
            scan_fields = [
                ("given", scenario.get("given") or []),
                ("when", scenario.get("when") or []),
                ("then", scenario.get("then") or []),
                ("evidence", scenario.get("evidence") or []),
            ]
            for fname, fval in scan_fields:
                hits = _scan_value_for_hardcoded(fval, patterns, fname)
                for hit_kind, hit_str in hits:
                    code = "scenario_given_hardcoded_endpoint"
                    msg = (
                        f"{filename} → scenarios[{s_idx}] ({scenario_id}) → "
                        f"{fname} 字段含硬编码 {hit_kind}: {hit_str!r}; "
                        f"必须改用 {{env.<段>.<name>.<field>}} 占位引用 env-description.yaml 资源"
                    )
                    issues.append((code, msg))

    return issues


def _scan_value_for_hardcoded(value, patterns, field_name: str):
    """递归扫描 list / dict / str, 返回命中的 (hit_kind, hit_str) 列表."""
    hits: list[tuple[str, str]] = []
    if isinstance(value, list):
        for item in value:
            hits.extend(_scan_value_for_hardcoded(item, patterns, field_name))
    elif isinstance(value, dict):
        for k, v in value.items():
            hits.extend(_scan_value_for_hardcoded(v, patterns, field_name))
    elif isinstance(value, str):
        stripped = value.lstrip()
        if not stripped:
            return hits
        # 注释行豁免
        if stripped.startswith("#"):
            return hits
        # 占位符 / URL scheme 前缀豁免
        if any(stripped.startswith(p) for p in _HARDCODED_ALLOW_PREFIXES):
            return hits
        # 整串只指向本地地址豁免 (含 host:port 形式)
        if any(host in stripped for host in _HARDCODED_LOCAL_ALLOWLIST):
            return hits
        for kind, pattern in patterns:
            for m in pattern.finditer(value):
                hit_str = m.group(0)
                # 本地 IP 豁免
                if kind == "ipv4" and hit_str in _HARDCODED_LOCAL_ALLOWLIST:
                    continue
                # http_endpoint 含 localhost / 127.0.0.1 / 0.0.0.0 时豁免
                if kind == "http_endpoint":
                    if any(host in hit_str for host in _HARDCODED_LOCAL_ALLOWLIST):
                        continue
                # port_literal 含 port<1000 豁免 (测试常用 80 / 443 / 3000)
                if kind == "port_literal":
                    port_match = re.search(r"\b\d{4,5}\b", hit_str)
                    if port_match:
                        port_num = int(port_match.group(0))
                        if port_num < 1000:
                            continue
                hits.append((kind, hit_str))
    return hits


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


# ============================================================
# v3.x: design-api 子命令 — 校验 design.md 的 API 端点是否含完整 Request/Response Body
# ============================================================

import re as _re


def _extract_api_endpoints(design_text):
    """从 design.md 提取 `## API 设计` 章节下的所有 endpoint 标题（含子段 body）。

    主端点的特征：标题以 HTTP method 开头（如 "### POST /api/..."），
    或标题不含 " - Request/Response Body" 后缀。

    返回 [(level, title, body), ...]：
    - level: 标题级别（3 = ###）
    - title: 端点标题（如 "POST /api/...")
    - body: 该端点下到下个主端点为止的所有内容（含子段）
    """
    api_section_match = _re.search(
        r"^## API 设计\s*\n(.*?)(?=^## |\Z)",
        design_text, _re.MULTILINE | _re.DOTALL,
    )
    if not api_section_match:
        return []

    api_section = api_section_match.group(1)
    h3_pattern = _re.compile(r"^(### .+)$", _re.MULTILINE)
    matches = list(h3_pattern.finditer(api_section))

    # 第一遍：识别哪些是子段标题
    subsegment_indices = set()
    for i, m in enumerate(matches):
        title = m.group(1).lstrip("# ").strip()
        if _is_subsegment_title(title):
            subsegment_indices.add(i)

    # 第二遍：主端点 = 非子段索引；body 范围到下个主端点
    endpoints = []
    main_indices = [i for i in range(len(matches)) if i not in subsegment_indices]
    for k, i in enumerate(main_indices):
        m = matches[i]
        title = m.group(1).lstrip("# ").strip()
        start = m.end()
        if k + 1 < len(main_indices):
            end = matches[main_indices[k + 1]].start()
        else:
            end = len(api_section)
        body = api_section[start:end]
        endpoints.append((3, title, body))

    return endpoints


def _is_subsegment_title(title):
    """检查标题是否是 endpoint 的子段（如 "### POST /api/foo - Request Body"）。

    子段以 " - Request Body"、" - Response Body"、" - Response Body (200)" 等后缀结尾，
    应跳过独立校验（这些段由对应父端点的子检查项覆盖）。
    """
    subsegment_suffixes = (
        " - Request Body",
        " - Response Body",
        " - Response",
        " - Request",
    )
    if any(title.endswith(s) for s in subsegment_suffixes):
        return True
    # 也匹配 "- Response Body (200)"、"- Response Body (4xx)" 这类带括号后缀
    if _re.search(r"-\s+(Request|Response)(\s+Body)?\s*\(.*\)$", title):
        return True
    return False


def _endpoint_mentions_http(title, body):
    """检查 endpoint 标题或 body 是否标识 HTTP 端点。

    接受三种格式：
      1. 标题本身含 HTTP method：`### POST /api/foo/bar`（推荐格式）
      2. body 内含 `**METHOD /path**` 内联 code：
         ```
         ### 创建 Project
         **POST /api/project.../v3/tenants/{tenantId}/projects**
         ```
      3. body 内含 `` `METHOD /path` `` 反引号 code：
         ```
         ### 创建 Project
         **`POST /api/...`**
         ```
    """
    http_methods = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
    # 1. 标题含 method
    for m in http_methods:
        if _re.search(rf"\b{m}\b\s+/", title):
            return True
    # 2. body 含 **METHOD /path**（粗体）
    for m in http_methods:
        if _re.search(rf"\*\*{m}\s+/", body):
            return True
    # 3. body 含 `METHOD /path`（反引号 inline code）
    for m in http_methods:
        if _re.search(rf"`{m}\s+/", body):
            return True
    return False


def _body_has_section(body, section_keyword):
    """检查 endpoint body 是否含指定段落（如 'Request Body', 'Response Body'）。

    接受以下格式：
      ### POST /api/foo - Request Body
      **Request Body**: ...
      Request Body: ...
    """
    patterns = [
        rf"###\s+.*-\s+{section_keyword}",
        rf"\*\*{section_keyword}\*\*\s*[:：]",
        rf"^{section_keyword}\s*[:：]",
    ]
    return any(_re.search(p, body, _re.MULTILINE | _re.IGNORECASE) for p in patterns)


def _body_has_json_example(body):
    """检查 endpoint body 是否含至少一个 JSON 代码块。"""
    return bool(_re.search(r"```(?:json)?\s*\n", body))


def _resolve_change_root(change):
    """解析 change 根目录路径，fallback 到 archive 子目录。

    优先：`<CHANGES_DIR>/<change>`
    fallback：`<CHANGES_DIR>/archive/*-<change>` （取最新一个，按字典序）
    """
    direct = os.path.join(CHANGES_DIR, change)
    if os.path.isfile(os.path.join(direct, "design.md")):
        return direct

    # fallback: archive 下 glob 匹配（archive 内目录格式 `<date>-<change>`）
    import glob as _glob
    candidates = _glob.glob(os.path.join(CHANGES_DIR, "archive", f"*-{change}"))
    if candidates:
        # 取最新的（按目录名字典序）
        candidates.sort(reverse=True)
        if os.path.isfile(os.path.join(candidates[0], "design.md")):
            return candidates[0]

    return direct  # 仍返回 direct 让上层 ERROR 信息一致


def cmd_design_api(change):
    """Validate design.md API endpoint coverage (Request + Response Body).

    v3.x 新增：每个 API 端点必须含完整 Request Body 与 Response Body JSON 示例。
    缺 Response Body → exit 1（on-conditions 阶段会要求 refine）。
    """
    change_root = _resolve_change_root(change)
    design_path = os.path.join(change_root, "design.md")

    if not os.path.isfile(design_path):
        print(f"ERROR: design.md 不存在: {design_path}", file=sys.stderr)
        sys.exit(1)

    with open(design_path, encoding="utf-8") as f:
        design_text = f.read()

    endpoints = _extract_api_endpoints(design_text)

    if not endpoints:
        # 无 API 设计章节（可能纯前端 / 纯内部重构）→ 跳过
        print("OK: design.md 无 API 设计章节，跳过 API Contract 校验")
        sys.exit(0)

    issues = []
    endpoint_count = 0
    for level, title, body in endpoints:
        # 跳过 endpoint 的子段标题（"- Request Body" / "- Response Body"）
        if _is_subsegment_title(title):
            continue
        if not _endpoint_mentions_http(title, body):
            # 非 HTTP 端点（如 "### 数据模型" 标题在 API 设计章节下）→ 跳过
            continue

        endpoint_count += 1

        # 1. 必填 Request Body
        if not _body_has_section(body, "Request Body"):
            issues.append(
                f"endpoint '{title}' 缺 Request Body 段（必填）"
            )

        # 2. 必填 Response Body
        if not _body_has_section(body, "Response Body"):
            issues.append(
                f"endpoint '{title}' 缺 Response Body 段（必填）"
            )

        # 3. 必填 JSON 示例（Request 或 Response 至少一处）
        if not _body_has_json_example(body):
            issues.append(
                f"endpoint '{title}' 缺 JSON 代码块（请求/响应示例）"
            )

    print(f"\n设计 API 覆盖率校验:")
    print(f"  HTTP 端点总数: {endpoint_count}")
    print(f"  问题数: {len(issues)}")

    if issues:
        print(f"\nFAILED: {len(issues)} issue(s) found:", file=sys.stderr)
        for i, msg in enumerate(issues, 1):
            print(f"  {i}. {msg}", file=sys.stderr)
        print(
            "\n修复指引: 参考 `.pg/skills/src/core/workflows/skills/pg-propose/references/design-templates.md` "
            "的 `## API 设计` 章节模板。",
            file=sys.stderr,
        )
        sys.exit(1)

    print("OK: all endpoints have Request/Response Body coverage")


_V_ID_RE = re.compile(r"\bV-([a-zA-Z][a-zA-Z0-9]*(?:[-_][a-zA-Z0-9]+)*)\b")


def _check_env_resource_refs_usage(
    change: str,
    design_text: str,
    scenario_files: list[str],
    define_summary_path: str | None,
) -> list[tuple[str, str]]:
    """v1.3: 强引用 define-summary.yaml 中已声明的 env_resource_refs.

    规则:
      - define-summary.yaml 不存在 → 跳过本规则 (旧 change 可能无 define 产物)
      - define-summary 中 verification_needs[].env_resource_refs 为空 → 跳过
      - design.md "环境限制与验证策略" 段未引用任何 env_resource_refs → WARN
      - 所有 scenario-*.yaml 联合未引用任何 env_resource_refs → WARN
      - 单 ref 维度不强制覆盖 (LLM 可合理选择不引用某些 ref)
    """
    issues: list[tuple[str, str]] = []
    if not define_summary_path or not os.path.isfile(define_summary_path):
        return issues
    if yaml is None:
        return issues
    try:
        with open(define_summary_path, encoding="utf-8") as f:
            ds = yaml.safe_load(f) or {}
        vns = ds.get("verification_needs") or []
        ref_set: set[str] = set()
        for v in vns:
            if not isinstance(v, dict):
                continue
            for ref in v.get("env_resource_refs") or []:
                if isinstance(ref, str):
                    ref_set.add(ref)
    except Exception:
        return issues
    if not ref_set:
        return issues

    # 抽取 design.md "环境限制与验证策略" 段 (从该 heading 到下一个二级 heading)
    seg_match = re.search(
        r"^###\s+环境限制与验证策略\s*\n(.*?)(?=^###\s|\Z)",
        design_text, re.MULTILINE | re.DOTALL,
    )
    design_segment = seg_match.group(1) if seg_match else design_text
    design_refs = set(_ENV_REF_PATTERN.findall(design_segment))
    design_ref_full = set(m.group(0) for m in _ENV_REF_PATTERN.finditer(design_segment))

    if not design_refs:
        # fallback: 在整篇 design.md 中找, 避免 "段没匹配但全文有" 误报
        design_refs = set(_ENV_REF_PATTERN.findall(design_text))
        design_ref_full = set(m.group(0) for m in _ENV_REF_PATTERN.finditer(design_text))

    # design 段中 {env.<段>[name=<x>]...} 的 (seg, name) 与 define-summary 中的 ref 比对
    def _parse_ref(ref: str) -> tuple[str, str]:
        m = re.match(
            r"\{env\.(infra_services|business_systems|data_resources|config_resources|"
            r"runtime_environment|external_dependencies)\[([^\]]+)\]",
            ref,
        )
        return (m.group(1), m.group(2)) if m else ("", "")

    ds_pairs = {_parse_ref(r) for r in ref_set}
    design_pairs = set(design_refs)

    if ds_pairs and not (ds_pairs & design_pairs):
        issues.append((
            "env_resource_refs_design_unused",
            "design.md 未引用 define-summary 中任何 env_resource_refs (定义: {})".format(
                ", ".join(sorted(ref_set)),
            ),
        ))

    # scenario-*.yaml 联合检查
    scenario_ref_full: set[str] = set()
    for sf in scenario_files:
        try:
            with open(sf, encoding="utf-8") as f:
                doc = yaml.safe_load(f)
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        for sc in doc.get("scenarios") or []:
            for key in ("given", "when", "then", "evidence"):
                val = sc.get(key)
                if isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict):
                            for sub in ("url", "value", "expression", "expected"):
                                v = item.get(sub)
                                if isinstance(v, str):
                                    scenario_ref_full.update(
                                        m.group(0) for m in _ENV_REF_PATTERN.finditer(v)
                                    )
                        elif isinstance(item, str):
                            scenario_ref_full.update(
                                m.group(0) for m in _ENV_REF_PATTERN.finditer(item)
                            )

    scenario_pairs = set(_ENV_REF_PATTERN.findall(" ".join(scenario_ref_full)))
    if ds_pairs and not (ds_pairs & scenario_pairs):
        issues.append((
            "env_resource_refs_scenario_unused",
            "所有 scenario-*.yaml 联合未引用 define-summary 中任何 env_resource_refs (定义: {})".format(
                ", ".join(sorted(ref_set)),
            ),
        ))

    return issues


def _check_v_identifier_consistency(
    change: str,
    design_text: str,
    scenario_files: list[str],
) -> list[tuple[str, str]]:
    """建议 7: V-* 唯一化与对齐校验.

    规则 1 (v_identifier_duplicate): Verification Criteria 段内同一 V-* ID 出现多次 → ERROR
    规则 2 (v_identifier_covers_not_in_design): scenario covers 引用的 V-*
        不在 design.md 任何位置 → ERROR
    规则 3 (v_identifier_naming_inconsistent): design.md 中同时存在
        下划线描述后缀和连字符描述后缀 → WARN

    Returns: list of (code, msg)
    """
    issues: list[tuple[str, str]] = []

    v_ids_in_design = set(_V_ID_RE.findall(design_text))

    vc_match = re.search(
        r"^## Verification Criteria\s*\n(.*?)(?=^## |\Z)",
        design_text, re.MULTILINE | re.DOTALL,
    )
    if vc_match:
        from collections import Counter
        vc_ids = _V_ID_RE.findall(vc_match.group(1))
        counts = Counter(vc_ids)
        for vid, cnt in counts.items():
            if cnt > 1:
                issues.append((
                    "v_identifier_duplicate",
                    f"design.md Verification Criteria 段中 V-{vid} 出现 {cnt} 次（应唯一）",
                ))

    for sf in scenario_files:
        try:
            with open(sf, encoding="utf-8") as f:
                doc = yaml.safe_load(f)
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        fname = os.path.basename(sf)
        for sc in doc.get("scenarios") or []:
            sid = sc.get("scenario_id", "?")
            for cover in sc.get("covers") or []:
                m = re.match(r"^V-(.+)$", str(cover))
                if m and m.group(1) not in v_ids_in_design:
                    issues.append((
                        "v_identifier_covers_not_in_design",
                        f"{fname}: {sid} covers 引用 V-{m.group(1)}，但 design.md 中不存在",
                    ))

    has_underscore = any("_" in vid for vid in v_ids_in_design)
    has_hyphen_desc = any(
        re.search(r"-[a-zA-Z]", vid) for vid in v_ids_in_design
        if not re.match(r"^[a-z]+-[a-z]+-\d+$", vid)
    )
    if has_underscore and has_hyphen_desc:
        issues.append((
            "v_identifier_naming_inconsistent",
            "design.md 中 V-* 描述后缀混用了下划线和连字符，建议统一为连字符",
        ))

    return issues


def _check_risk_criteria_alignment(
    change: str,
    proposal_text: str,
    design_text: str,
) -> list[tuple[str, str]]:
    r"""建议 8: proposal R-* 风险与 design Verification Criteria 交叉校验.

    规则 1 (risk_criteria_missing): proposal R-* 描述中提到的 V-* ID
        不在 design.md Verification Criteria 表中 → ERROR
    规则 2 (criteria_no_risk_coverage): design Verification Criteria 表中的 V-*
        未被任何 proposal R-* 引用 → WARN

        v1.1 降噪: 旧实现按每个未覆盖 V-* 逐条 WARN (20 个 V-* → 20 条噪声).
        新实现聚合为单条 summary WARN; 且当 proposal R-* 风险表本身未使用
        R\d+ 编号格式时 (无法建立交叉引用), 整条规则跳过 — 交叉引用是可选
        实践, 不应因格式差异产生逐条误报.

    Returns: list of (code, msg)
    """
    issues: list[tuple[str, str]] = []

    r_row_re = re.compile(r"^\|\s*(R\d+)\s*\|(.*?)\|", re.MULTILINE)
    v_in_risks: dict[str, list[str]] = {}
    for m in r_row_re.finditer(proposal_text):
        r_id = m.group(1)
        row_text = m.group(2)
        for vid in _V_ID_RE.findall(row_text):
            v_in_risks.setdefault(vid, []).append(r_id)

    vc_match = re.search(
        r"^## Verification Criteria\s*\n(.*?)(?=^## |\Z)",
        design_text, re.MULTILINE | re.DOTALL,
    )
    v_in_criteria: set[str] = set()
    if vc_match:
        v_in_criteria = set(_V_ID_RE.findall(vc_match.group(1)))

    for vid, r_ids in sorted(v_in_risks.items()):
        if vid not in v_in_criteria:
            issues.append((
                "risk_criteria_missing",
                f"proposal {','.join(r_ids)} 引用 V-{vid}，"
                f"但 design.md Verification Criteria 表中不存在",
            ))

    # 规则 2: 仅当 proposal 存在 R\d+ 编号风险表时才做反向覆盖检查
    has_numbered_risks = bool(r_row_re.search(proposal_text))
    if v_in_criteria and has_numbered_risks:
        uncovered = sorted(v_in_criteria - set(v_in_risks.keys()))
        if uncovered:
            issues.append((
                "criteria_no_risk_coverage",
                f"design.md 中 {len(uncovered)}/{len(v_in_criteria)} 个 V-* "
                f"未被任何 proposal R-* 风险引用（建议核对风险覆盖, "
                f"未覆盖示例: {', '.join(uncovered[:5])}）",
            ))

    return issues


def _check_define_summary_propagation(
    change: str,
    design_text: str,
    proposal_text: str,
    scenario_files: list[str],
    ds_path: str | None,
) -> list[tuple[str, str]]:
    """PR-B2: define-summary.yaml post_discussion_status 三态 → 产物契约校验.

    规则:
      1. verifiable 的 V-* id 必须出现在至少一个 scenario-<track>.yaml 的 covers 中
         → 否则 define_summary_verifiable_uncovered (ERROR)
      2. skipped 的 V-* id 必须出现在 proposal.md「风险和注意事项」或「未做」段
         → 否则 define_summary_skipped_not_in_proposal (ERROR)
      3. degraded 的 V-* id 必须出现在 design.md「环境限制与验证策略」段
         (H3 + 表格行, 表格列含 V-* id)
         → 否则 define_summary_degraded_no_fallback (ERROR)

    define-summary.yaml 不存在 → 跳过 (向后兼容, 旧 change 无 define 产物)
    """
    issues: list[tuple[str, str]] = []
    if not ds_path or not os.path.isfile(ds_path):
        return issues
    if yaml is None:
        return issues
    try:
        with open(ds_path, encoding="utf-8") as f:
            ds = yaml.safe_load(f) or {}
    except Exception:
        return issues

    verifiable: list[str] = []
    degraded: list[str] = []
    skipped: list[str] = []
    for v in ds.get("verification_needs") or []:
        if not isinstance(v, dict):
            continue
        vid = v.get("id")
        status = v.get("post_discussion_status")
        if not isinstance(vid, str):
            continue
        if status == "verifiable":
            verifiable.append(vid)
        elif status == "degraded":
            degraded.append(vid)
        elif status == "skipped":
            skipped.append(vid)

    # 规则 1: verifiable 的 V-* id 必须出现在 scenario covers
    covered_v_ids: set[str] = set()
    for sf in scenario_files:
        try:
            with open(sf, encoding="utf-8") as f:
                doc = yaml.safe_load(f)
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        for sc in doc.get("scenarios") or []:
            if not isinstance(sc, dict):
                continue
            for cover in sc.get("covers") or []:
                if isinstance(cover, str):
                    covered_v_ids.add(cover)
    for vid in verifiable:
        if vid not in covered_v_ids:
            issues.append((
                "define_summary_verifiable_uncovered",
                "{}: post_discussion_status=verifiable, 但未在 scenario-*.yaml covers 中引用".format(vid),
            ))

    # 规则 2: skipped 的 V-* id 必须出现在 proposal.md「风险和注意事项」或「未做」段
    # 解析方式: 找 ## 风险和注意事项 / ## 未做 段, 收集段内所有 V-XXX ID
    skipped_section_re = re.compile(
        r"^##\s+(风险和注意事项|未做|不做)\s*\n(.*?)(?=^##\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    proposal_v_ids: set[str] = set()
    for m in skipped_section_re.finditer(proposal_text):
        for v_match in _V_ID_RE.finditer(m.group(2)):
            proposal_v_ids.add("V-" + v_match.group(1))
    for vid in skipped:
        if vid not in proposal_v_ids:
            issues.append((
                "define_summary_skipped_not_in_proposal",
                "{}: post_discussion_status=skipped, 但未在 proposal.md「风险和注意事项」/「未做」段列出".format(vid),
            ))

    # 规则 3: degraded 的 V-* id 必须出现在 design.md「环境限制与验证策略」段
    # 解析方式: 找 ### 环境限制与验证策略 段, 段内 markdown 表格行含 V-XXX ID
    degraded_section_re = re.compile(
        r"^###\s+环境限制与验证策略\s*\n(.*?)(?=^###\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    design_degraded_v_ids: set[str] = set()
    dm = degraded_section_re.search(design_text)
    if dm:
        section_body = dm.group(1)
        for v_match in _V_ID_RE.finditer(section_body):
            design_degraded_v_ids.add("V-" + v_match.group(1))
    for vid in degraded:
        if vid not in design_degraded_v_ids:
            issues.append((
                "define_summary_degraded_no_fallback",
                "{}: post_discussion_status=degraded, 但未在 design.md「环境限制与验证策略」段列出".format(vid),
            ))

    return issues


def _check_scenario_env_consistency(change: str, manifest: dict) -> list[tuple[str, str]]:
    """v1.0 (v6 hook 协议): 校验 scenario given 与 env-description.yaml 的一致性 (warning 级).

    取代旧 v0.9.0 基于 env-capability.yaml seed_data 的检查. 新规则基于 6 段结构:

      规则 1 (env_description_missing): .pg/changes/<change-id>/env-description.yaml 不存在
        → 提示先跑 pg-propose 阶段 1d.5 的 describe_env. warning, 不阻塞 (向后兼容)
      规则 2 (scenario_given_unknown_instance): given 引用的 instance id 不在
        infra_services.<name>.instances[].id 中 → warning
      规则 3 (scenario_given_data_status_mismatch): given 假设某种 data_resources 状态
        (如"已 seed" / "已有 row"), 但 env-description.yaml 中 state.status 为 empty/unknown
        → warning
      规则 4 (scenario_relation_undeclared): given 隐含资源间依赖 (eg. "X 依赖 Y 配置"),
        但 env-description.yaml 的 relations 段未声明 → info

    Returns: list of (code, msg) — empty list = OK.
    """
    issues: list[tuple[str, str]] = []

    # 1. 读取 env-description.yaml (per-change 特定)
    env_desc_path = os.path.join(
        CHANGES_DIR, change, "env-description.yaml"
    )
    if not os.path.isfile(env_desc_path):
        issues.append((
            "env_description_missing",
            f"{change}: 缺少 env-description.yaml, 请先跑 pg-propose 1d.5 "
            f"(调用 pg-invoke-hook.py --action describe_env)",
        ))
        return issues

    try:
        with open(env_desc_path, encoding="utf-8") as f:
            env_desc = yaml.safe_load(f)
    except Exception as e:
        issues.append((
            "env_description_invalid_yaml",
            f"{env_desc_path}: YAML 解析失败: {e}",
        ))
        return issues

    # 2. 确定目标 env
    if not manifest or "stages" not in manifest:
        return issues
    env_name = None
    for stage in manifest["stages"]:
        # 兼容历史 stage 名 (int / real-integration / dev-mock-integration)
        if stage.get("name") in ("int", "real-integration", "dev-mock-integration"):
            env_name = stage.get("environment")
            break
    if not env_name:
        return issues

    env_block = env_desc.get("environments", {}).get(env_name)
    if not isinstance(env_block, dict):
        issues.append((
            "env_description_env_missing",
            f"env-description.yaml 缺少 environments.{env_name} 段",
        ))
        return issues

    # 3. 收集所有 instance id (infra_services[*].instances[*].id)
    instance_ids: set[str] = set()
    for svc in env_block.get("infra_services", []) or []:
        for inst in svc.get("instances", []) or []:
            iid = inst.get("id")
            if iid:
                instance_ids.add(str(iid))

    # 4. 收集 data_resources 状态
    data_status: dict[str, str] = {}
    for dr in env_block.get("data_resources", []) or []:
        name = dr.get("name")
        status = (dr.get("state") or {}).get("status")
        if name:
            data_status[name] = status or "unknown"

    # 5. 收集 relations 中的 from/to 资源名集合
    declared_resources: set[str] = set()
    for rel in env_block.get("relations", []) or []:
        if rel.get("from"):
            declared_resources.add(rel["from"])
        if rel.get("to"):
            declared_resources.add(rel["to"])

    # 6. 遍历 scenario 文件
    import glob as _glob
    for scenario_path in _glob.glob(
        os.path.join(CHANGES_DIR, change, "scenario-*.yaml"),
    ):
        try:
            with open(scenario_path, encoding="utf-8") as f:
                scenario_doc = yaml.safe_load(f)
        except Exception:
            continue
        if not isinstance(scenario_doc, dict):
            continue
        for sc in scenario_doc.get("scenarios", []):
            if not isinstance(sc, dict):
                continue
            given_text = " ".join(sc.get("given", []) or [])
            sid = sc.get("scenario_id", "<unknown>")

            # 规则 2: given 引用的 instance id 不在 env-description 中
            id_matches = re.findall(r"id=([A-Za-z0-9_-]{3,})", given_text)
            for iid in id_matches:
                if iid not in instance_ids:
                    issues.append((
                        "scenario_given_unknown_instance",
                        f"{sid}: given 引用 instance id={iid}, "
                        f"但 {env_name} env-description.yaml 未声明",
                    ))

            # 规则 3: data_resources 状态假设
            for dr_name, dr_status in data_status.items():
                if dr_status == "empty" and dr_name in given_text and (
                    "已 seed" in given_text or "已存在" in given_text or "ready" in given_text.lower()
                ):
                    issues.append((
                        "scenario_given_data_status_mismatch",
                        f"{sid}: given 假设 {dr_name} 已 seed/存在, "
                        f"但 env-description.yaml 标记 status=empty "
                        f"(需由前置 scenario 创建)",
                    ))

    return issues


# ---------------------------------------------------------------------------
# define-summary.yaml 校验 (pg-1-define 产物, pg-propose 阶段 1.8 消费)
# ---------------------------------------------------------------------------

DEFINE_SUMMARY_REL_PATH = os.path.join("0-define", "define-summary.yaml")
# 候选路径: (1) PROJECT_ROOT 锚定 (渲染到 .opencode/ 的副本也能命中);
#           (2) 源码树相对路径 (单元测试 PG_PROJECT_ROOT 指向临时目录时命中).
DEFINE_SUMMARY_SCHEMA_CANDIDATES = [
    os.path.join(PROJECT_ROOT, ".pg", "skills", "src", "runtime", "spec",
                 "define-summary.schema.json"),
    os.path.join(
        os.path.dirname(_SCRIPT_DIR),  # skills/pg-propose/
        "..", "..", "..", "..", "..",  # → .pg/skills/ (workflows→core→src→skills)
        "src", "runtime", "spec", "define-summary.schema.json",
    ),
]
ENV_REF_RE = re.compile(
    r"^\{env\.(infra_services|business_systems|data_resources|config_resources|runtime_environment|external_dependencies)\[([^\]]+)\](.*)\}$"
)


def _load_define_summary_schema():
    """Load define-summary.schema.json; returns None on failure (caller degrades)."""
    for candidate in DEFINE_SUMMARY_SCHEMA_CANDIDATES:
        try:
            path = os.path.normpath(candidate)
            if not os.path.isfile(path):
                continue
            return _load_json_schema(path)
        except Exception:
            continue
    return None


def _validate_define_summary_structure(ds: dict, schema) -> list:
    """Structural checks for define-summary.yaml (no external lib dependency).

    ``schema`` may be None (schema load failed) — in that case no checks run.
    Returns list of (code, message).
    """
    issues = []

    if schema is None:
        return issues

    props = schema.get("properties", {})

    # 顶层必填字段
    for field in schema.get("required", []):
        if field not in ds:
            issues.append((
                "define_summary_missing_" + field,
                "缺少必填字段: " + field,
            ))

    # schema_version const
    if "schema_version" in ds:
        const_val = props.get("schema_version", {}).get("const")
        if const_val is not None and ds["schema_version"] != const_val:
            issues.append((
                "define_summary_schema_version_mismatch",
                "schema_version 必须为 {}, 实际: {!r}".format(
                    const_val, ds["schema_version"]),
            ))

    # defined_by const
    if "defined_by" in ds:
        const_val = props.get("defined_by", {}).get("const")
        if const_val is not None and ds["defined_by"] != const_val:
            issues.append((
                "define_summary_defined_by_mismatch",
                "defined_by 必须为 {!r}, 实际: {!r}".format(
                    const_val, ds["defined_by"]),
            ))

    # change_id pattern
    cid = ds.get("change_id")
    if cid is not None and not isinstance(cid, str):
        issues.append(("define_summary_change_id_not_string",
                       "change_id 必须是字符串"))
    elif isinstance(cid, str):
        pat = props.get("change_id", {}).get("pattern")
        if pat and not re.match(pat, cid):
            issues.append((
                "define_summary_change_id_bad_pattern",
                "change_id {!r} 不匹配 pattern {!r}".format(cid, pat),
            ))

    # verification_needs 数组
    vn = ds.get("verification_needs")
    if vn is not None:
        if not isinstance(vn, list):
            issues.append((
                "define_summary_verification_needs_not_array",
                "verification_needs 必须是数组",
            ))
        else:
            vn_schema = props.get("verification_needs", {}).get("items", {})
            vn_required = vn_schema.get("required", [])
            seen_ids = set()
            for i, item in enumerate(vn):
                if not isinstance(item, dict):
                    issues.append((
                        "define_summary_vn_item_not_object",
                        "verification_needs[{}] 必须是对象".format(i),
                    ))
                    continue
                for field in vn_required:
                    if field not in item:
                        issues.append((
                            "define_summary_vn_missing_" + field,
                            "verification_needs[{}] 缺少必填字段: {}".format(i, field),
                        ))
                # id 唯一性 + pattern
                vid = item.get("id")
                if isinstance(vid, str):
                    if vid in seen_ids:
                        issues.append((
                            "define_summary_vn_duplicate_id",
                            "verification_needs 存在重复 id: {}".format(vid),
                        ))
                    seen_ids.add(vid)
                    # 优先用 schema 内的 pattern; 若 schema 加载失败, 用内置 regex 兜底
                    id_pat = (vn_schema.get("properties", {})
                              .get("id", {}).get("pattern"))
                    if id_pat is None:
                        id_pat = r"^V-(?:[a-z][a-z0-9]*)(?:-[a-z][a-z0-9]+)*-(?:\d+)(?:-[a-z][a-z0-9]+)*$"
                    if not re.match(id_pat, vid):
                        issues.append((
                            "define_summary_vn_id_bad_pattern",
                            "verification_needs[{}].id {!r} 不匹配 pattern {!r}".format(
                                i, vid, id_pat),
                        ))
                    # id 与 track_id 前缀一致性 (schema regex 已强制 V-{track_id}-{seq}
                    # 形态, 此处只需校验 id 中 track 段与显式 track_id 一致)
                    if vid.startswith("V-"):
                        # 拆 track 段: 必须匹配到第一个 -<digits> 之前 (即 seq 之前)
                        m = re.match(
                            r"^V-([a-z][a-z0-9]*(?:-[a-z][a-z0-9]+)*)-\d+",
                            vid,
                        )
                        if m:
                            derived_track = m.group(1)
                            track_id = item.get("track_id")
                            if track_id is None:
                                # PR-C1: track_id 字段可选, 省略时自动派生
                                pass
                            elif isinstance(track_id, str) and track_id != derived_track:
                                issues.append((
                                    "define_summary_vn_track_id_mismatch",
                                    "verification_needs[{}].id={!r} 与 track_id={!r} 前缀不一致 (省略 track_id 即可自动派生)".format(
                                        i, vid, track_id),
                                ))
    return issues


def _validate_define_summary_env_refs(ds: dict, env_desc: dict) -> list:
    """Cross-check env_resource_refs against env-description.yaml.

    规则:
      - post_discussion_status=verifiable 且 env_resource_refs 为空 → error
      - env_resource_refs 引用的 <段>/<name> 不在 env-description 中 → error
      - post_discussion_status != verifiable 且 env_resource_refs 非空 → error
    """
    issues = []
    target_env = ds.get("target_environment")
    if not target_env:
        return issues

    env_block = (env_desc.get("environments") or {}).get(target_env)
    if not isinstance(env_block, dict):
        # env 不存在于 env-description → 上层已报 env_description_env_missing
        return issues

    # 收集 env-description 中各段已声明的资源名
    known_names = {}
    for seg in ("infra_services", "business_systems", "data_resources",
                "config_resources", "runtime_environment", "external_dependencies"):
        names = set()
        for res in env_block.get(seg, []) or []:
            if isinstance(res, dict) and res.get("name"):
                names.add(str(res["name"]))
        known_names[seg] = names

    for vn in ds.get("verification_needs", []) or []:
        if not isinstance(vn, dict):
            continue
        vid = vn.get("id", "<unknown>")
        status = vn.get("post_discussion_status")
        refs = vn.get("env_resource_refs") or []
        if refs is not None and not isinstance(refs, list):
            issues.append((
                "define_summary_refs_not_array",
                "{}: env_resource_refs 必须是数组".format(vid),
            ))
            continue
        refs = refs or []

        if status == "verifiable" and not refs:
            issues.append((
                "define_summary_verifiable_missing_refs",
                "{}: post_discussion_status=verifiable 时 env_resource_refs 不能为空".format(vid),
            ))
            continue
        if status != "verifiable" and refs:
            issues.append((
                "define_summary_non_verifiable_has_refs",
                "{}: post_discussion_status={} 时 env_resource_refs 必须为空".format(
                    vid, status),
            ))
            continue

        for ref in refs:
            m = ENV_REF_RE.match(ref) if isinstance(ref, str) else None
            if not m:
                issues.append((
                    "define_summary_ref_bad_format",
                    "{}: env_resource_refs 元素 {!r} 不匹配 {{env.<段>[name=<资源名>]…}} 格式".format(
                        vid, ref),
                ))
                continue
            seg, bracket, _rest = m.group(1), m.group(2), m.group(3)
            # bracket 形如 name=object-storage
            name_m = re.match(r"name=([^,\]]+)", bracket)
            if not name_m:
                issues.append((
                    "define_summary_ref_missing_name",
                    "{}: env_resource_refs 元素 {!r} 的括号内必须含 name=<资源名>".format(
                        vid, ref),
                ))
                continue
            rname = name_m.group(1).strip()
            if rname not in known_names.get(seg, set()):
                issues.append((
                    "define_summary_ref_unknown_resource",
                    "{}: env_resource_refs 引用 {}[name={}], 但 env-description.yaml "
                    "environments.{} 未声明该资源".format(vid, seg, rname, target_env),
                ))
    return issues


def _validate_define_summary_capabilities(ds: dict, env_desc: dict) -> list:
    """PR-A2: requires_capabilities ↔ env-description capabilities 交叉校验.

    规则:
      - 收集 env-description.environments.<env> 三段中每资源的 capabilities[] (infra_services /
        business_systems / data_resources)
      - 计数策略: postgresql / object_storage / redis_cache / k8s_cluster 这类
        "基础设施型" capability 按 infra_service.instances[] 长度累加;
        multi_tenant_data / sample_dataset 这类 "数据型" capability 按 resource 个数累加
        (不论 instances 多少)
      - 若 requires_capabilities[].capability 未在环境能力集合中找到 → error
      - 找到但 quantity < min_quantity → error
      - 描述仅用于提示, 不参与对账

    决定 "基础设施型 vs 数据型" 不可静态推断, 这里采用折中:
      - infra_services[*].capabilities: 按 instances 数量累加
      - business_systems[*].capabilities: 按 1 累加 (端点已存在即满足)
      - data_resources[*].capabilities: 按 1 累加
    若项目有更细策略, 可在 schema 后续版本加 capability_kind 字段.
    """
    issues = []
    target_env = ds.get("target_environment")
    if not target_env:
        return issues

    env_block = (env_desc.get("environments") or {}).get(target_env)
    if not isinstance(env_block, dict):
        return issues

    # 收集 capability 计数
    capability_count: dict[str, int] = {}

    def _accumulate(cap_list, multiplier):
        if not isinstance(cap_list, list):
            return
        for cap in cap_list:
            if isinstance(cap, str) and cap:
                capability_count[cap] = capability_count.get(cap, 0) + multiplier

    for svc in env_block.get("infra_services", []) or []:
        if not isinstance(svc, dict):
            continue
        instances = svc.get("instances", []) or []
        _accumulate(svc.get("capabilities"), len(instances))

    for biz in env_block.get("business_systems", []) or []:
        if not isinstance(biz, dict):
            continue
        _accumulate(biz.get("capabilities"), 1)

    for dr in env_block.get("data_resources", []) or []:
        if not isinstance(dr, dict):
            continue
        _accumulate(dr.get("capabilities"), 1)

    # 对每个 requires_capabilities 校验
    for vn in ds.get("verification_needs", []) or []:
        if not isinstance(vn, dict):
            continue
        vid = vn.get("id", "<unknown>")
        requires = vn.get("requires_capabilities") or []
        if not isinstance(requires, list):
            continue
        for req in requires:
            if not isinstance(req, dict):
                continue
            cap = req.get("capability")
            min_q = req.get("min_quantity", 1)
            if not isinstance(cap, str) or not cap:
                continue
            available = capability_count.get(cap, 0)
            if available == 0:
                issues.append((
                    "define_summary_capability_unsatisfied",
                    "{}: requires_capability={!r} 在 env-description 中未声明 (请检查 "
                    "infra_services/business_systems/data_resources 的 capabilities 字段,"
                    "或在 describe_env 脚本补充)".format(vid, cap),
                ))
                continue
            if isinstance(min_q, int) and available < min_q:
                issues.append((
                    "define_summary_capability_quantity_insufficient",
                    "{}: requires_capability={!r} min_quantity={}, 但环境仅提供 {} 个".format(
                        vid, cap, min_q, available),
                ))

    return issues


def cmd_define_summary(change):
    """Validate .pg/changes/<change>/0-define/define-summary.yaml (阶段 1.8 产物校验).

    校验维度:
      1. 文件存在性 (不存在 → error, 提示用户先在 pg-1-define 定界环节落盘)
      2. YAML 可解析性
      3. 结构校验 (对照 define-summary.schema.json, 无外部库)
      4. env_resource_refs ↔ env-description.yaml 交叉校验
      5. target_environment 与 env-description described_for.environment 一致性
      6. requires_capabilities ↔ env-description capabilities 交叉校验 (PR-A2)

    Exit 0 = valid, 1 = invalid.
    """
    if yaml is None:
        print("ERROR: PyYAML 未安装, 无法校验 define-summary.yaml", file=sys.stderr)
        sys.exit(1)

    ds_path = os.path.join(CHANGES_DIR, change, DEFINE_SUMMARY_REL_PATH)
    all_issues = []

    # 1. 存在性
    if not os.path.isfile(ds_path):
        print("ERROR: define-summary.yaml 不存在: {}".format(ds_path), file=sys.stderr)
        print("  请先在 pg-1-define 定界环节落盘 (用户授权后调 describe_env 并生成)", file=sys.stderr)
        sys.exit(1)

    # 2. 解析
    try:
        with open(ds_path, encoding="utf-8") as f:
            ds = yaml.safe_load(f)
    except Exception as e:
        print("ERROR: define-summary.yaml 解析失败: {}".format(e), file=sys.stderr)
        sys.exit(1)
    if not isinstance(ds, dict):
        print("ERROR: define-summary.yaml 顶层必须是 object", file=sys.stderr)
        sys.exit(1)

    # 3. 结构校验
    schema = _load_define_summary_schema()
    if schema is None:
        print("WARN: define-summary.schema.json 加载失败 (跳过结构校验)", file=sys.stderr)
    struct_issues = _validate_define_summary_structure(ds, schema)
    for code, msg in struct_issues:
        print("  [{}] {}".format(code, msg), file=sys.stderr)
        all_issues.append(code)

    # 4. change_id 与实际 change 目录一致性
    if ds.get("change_id") and ds["change_id"] != change:
        code = "define_summary_change_id_dir_mismatch"
        print("  [{}] change_id={!r} 与目录名 {!r} 不一致".format(
            code, ds["change_id"], change), file=sys.stderr)
        all_issues.append(code)

    # 5. env-description 交叉校验
    env_desc_path = os.path.join(CHANGES_DIR, change, "env-description.yaml")
    if os.path.isfile(env_desc_path):
        try:
            with open(env_desc_path, encoding="utf-8") as f:
                env_desc = yaml.safe_load(f) or {}
        except Exception as e:
            print("  [env_description_invalid_yaml] env-description.yaml 解析失败: {}".format(e),
                  file=sys.stderr)
            all_issues.append("env_description_invalid_yaml")
            env_desc = {}

        # 5a. target_environment 一致性
        described_env = ((env_desc.get("described_for") or {}).get("environment"))
        target_env = ds.get("target_environment")
        if described_env and target_env and described_env != target_env:
            code = "define_summary_env_mismatch"
            print("  [{}] define-summary target_environment={!r} 与 env-description "
                  "described_for.environment={!r} 不一致".format(
                      code, target_env, described_env), file=sys.stderr)
            all_issues.append(code)

        # 5b. env_resource_refs 交叉校验
        ref_issues = _validate_define_summary_env_refs(ds, env_desc)
        for code, msg in ref_issues:
            print("  [{}] {}".format(code, msg), file=sys.stderr)
            all_issues.append(code)

        # 5c. requires_capabilities ↔ env-description capabilities 交叉校验 (PR-A2)
        cap_issues = _validate_define_summary_capabilities(ds, env_desc)
        for code, msg in cap_issues:
            print("  [{}] {}".format(code, msg), file=sys.stderr)
            all_issues.append(code)
    else:
        print("  [env_description_missing] env-description.yaml 不存在, "
              "跳过 env_resource_refs 交叉校验 (请先跑 pg-1-define 定界环节的 describe_env)",
              file=sys.stderr)
        all_issues.append("env_description_missing")

    if all_issues:
        print("\nERROR: define-summary.yaml 校验失败, 共 {} 个问题: {}".format(
            len(all_issues), ", ".join(all_issues)), file=sys.stderr)
        # PR-B1: 给用户/agent 可执行的回退路径
        print(
            "\n  → 修复方式: /1-pg-define --redefine {}  (详见 pg-define SKILL §重新定界协议)".format(change),
            file=sys.stderr,
        )
        sys.exit(1)

    print("OK: define-summary.yaml 校验通过 (change={})".format(change))
    sys.exit(0)


def cmd_manifest(change):
    """Validate execution-manifest.yaml consistency."""
    manifest_path = os.path.join(CHANGES_DIR, change, "execution-manifest.yaml")
    tasks_path = os.path.join(CHANGES_DIR, change, "tasks.md")

    all_issues = []
    coverage_warnings: list[tuple[str, str]] = []  # v3.10: warning 级, 不阻塞
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

    # 7. v3.5: 三产物一致性校验
    consistency_issues = _validate_three_product_consistency(manifest, change)
    for code, msg in consistency_issues:
        print(f"  [{code}] {msg}", file=sys.stderr)
        all_issues.append(code)

    # 7.4 v0.8.4: 简化版机械校验（替代 review-notes 主观自审）
    # (v1.1: 合并原 v0.8.3/v0.8.4 两个重复块为单一实现, 消除 WARN 双报)
    extra_warnings: list[tuple[str, str]] = []

    # 规则 1: V-* ↔ verify 任务映射检查
    try:
        design_path = os.path.join(CHANGES_DIR, change, "design.md")
        if os.path.isfile(design_path):
            with open(design_path, encoding="utf-8") as _f:
                _design_text = _f.read()
            v_track_re = re.compile(r"V-([a-zA-Z0-9_-]+)-(\d+)")
            v_in_design = set(v_track_re.findall(_design_text))

            verify_v_re = re.compile(r"验证\s*V-([a-zA-Z0-9_-]+)-(\d+)")
            # 模板占位符: pg-gen-tasks-skeleton.py 生成的 verify 章节标准形式是
            # "验证 V-{track}-N：来自 design.md（N 由 design.md 决定，非章节号）",
            # 由 runner 在执行时解析 design.md 展开为具体 V-* — 该占位符视为
            # 整 track 已覆盖, 不报 v_identifier_uncovered.
            placeholder_re = re.compile(r"验证\s*V-([a-zA-Z0-9_-]+)-N")
            # section 无结构化 sub 字段, 从 heading "<stage>.<track>:<sub> - ..." 解析
            verify_prefix_re = re.compile(
                r"^##\s+\d+\.\s+[a-zA-Z0-9_.-]+:([a-zA-Z0-9_-]+)\s*-"
            )
            v_in_verify: set[tuple[str, str]] = set()
            placeholder_tracks: set[str] = set()
            for sec in tasks_sections:
                hm = verify_prefix_re.match(sec.get("heading", ""))
                if not hm or hm.group(1) != "verify":
                    continue
                body = sec.get("body", "")
                for m in verify_v_re.finditer(body):
                    v_in_verify.add((m.group(1), m.group(2)))
                for m in placeholder_re.finditer(body):
                    placeholder_tracks.add(m.group(1))

            track_total: dict[str, int] = {}
            track_covered: dict[str, int] = {}
            for (track, _) in v_in_design:
                track_total[track] = track_total.get(track, 0) + 1
            for (track, _) in v_in_verify:
                if track in v_in_design:
                    track_covered[track] = track_covered.get(track, 0) + 1

            for track in sorted(track_total.keys()):
                if track in placeholder_tracks:
                    continue  # 模板占位符 = runner 运行时展开, 视为已覆盖
                total = track_total[track]
                covered = track_covered.get(track, 0)
                if total > 0 and covered < total:
                    extra_warnings.append((
                        "v_identifier_uncovered",
                        f"track={track}: {covered}/{total} V-* 标识符被 verify 任务覆盖",
                    ))
    except Exception as _e:
        extra_warnings.append((
            "v_identifier_check_failed",
            f"V-* 映射检查异常: {_e}",
        ))

    # 规则 2: scenario-*.yaml 引用防护（防御 build 阶段任务描述误改 scenario）
    try:
        scenario_ref_re = re.compile(r"scenario-[a-zA-Z0-9_-]*\.yaml")
        for sec in tasks_sections:
            body = sec.get("body", "")
            for m in scenario_ref_re.finditer(body):
                ref = m.group(0)
                if "禁止" in body or "SSOT" in body or "如需修改" in body:
                    continue
                extra_warnings.append((
                    "scenario_yaml_referenced",
                    f"section {sec.get('section_key')!r} 引用 {ref} (scenario-*.yaml 必须通过 pg-gen-scenario.py 重新生成, 禁止任务代码修改)",
                ))
    except Exception as _e:
        extra_warnings.append((
            "scenario_reference_check_failed",
            f"scenario 引用检查异常: {_e}",
        ))

    # 规则 3: tasks.md 章节编号连续性
    try:
        heading_re = re.compile(r"^##\s+(\d+)\.\s+")
        nums: list[int] = []
        with open(tasks_path, encoding="utf-8") as _f:
            for _line in _f:
                m = heading_re.match(_line)
                if m:
                    nums.append(int(m.group(1)))
        if nums:
            expected = list(range(1, len(nums) + 1))
            if nums != expected:
                seen = set()
                dup = [n for n in nums if n in seen or seen.add(n)]
                skipped = [n for n in expected if n not in nums]
                if dup:
                    extra_warnings.append((
                        "tasks_md_section_duplicate",
                        f"tasks.md 章节编号重号: {sorted(set(dup))}",
                    ))
                if skipped:
                    extra_warnings.append((
                        "tasks_md_section_skipped",
                        f"tasks.md 章节编号跳号: 缺失 {skipped}",
                    ))
    except Exception as _e:
        extra_warnings.append((
            "tasks_md_section_check_failed",
            f"章节编号检查异常: {_e}",
        ))

    coverage_warnings.extend(extra_warnings)

    # 7.4c v1.3: env_resource_refs 强引用 (WARN 级)
    try:
        import glob as _glob_v2
        _ds_path_v2 = os.path.join(CHANGES_DIR, change, "0-define", "define-summary.yaml")
        _design_text_v2 = ""
        _design_path_v2 = os.path.join(CHANGES_DIR, change, "design.md")
        if os.path.isfile(_design_path_v2):
            with open(_design_path_v2, encoding="utf-8") as _f_v2:
                _design_text_v2 = _f_v2.read()
        _scenario_files_v2 = _glob_v2.glob(
            os.path.join(CHANGES_DIR, change, "scenario-*.yaml")
        )
        for code, msg in _check_env_resource_refs_usage(
            change, _design_text_v2, _scenario_files_v2, _ds_path_v2,
        ):
            coverage_warnings.append((code, msg))
    except Exception as _e_v2:
        coverage_warnings.append((
            "env_resource_refs_check_failed",
            f"env_resource_refs 校验异常: {_e_v2}",
        ))

    # 7.4b 建议 7+8: V-* 唯一化/对齐 + R-* 交叉校验
    try:
        _design_path_7 = os.path.join(CHANGES_DIR, change, "design.md")
        _proposal_path_7 = os.path.join(CHANGES_DIR, change, "proposal.md")
        _design_text_7 = ""
        _proposal_text_7 = ""
        if os.path.isfile(_design_path_7):
            with open(_design_path_7, encoding="utf-8") as _f7:
                _design_text_7 = _f7.read()
        if os.path.isfile(_proposal_path_7):
            with open(_proposal_path_7, encoding="utf-8") as _f7:
                _proposal_text_7 = _f7.read()

        if _design_text_7 and yaml is not None:
            import glob as _glob7
            _scenario_files_7 = _glob7.glob(
                os.path.join(CHANGES_DIR, change, "scenario-*.yaml")
            )
            for code, msg in _check_v_identifier_consistency(
                change, _design_text_7, _scenario_files_7,
            ):
                if code == "v_identifier_naming_inconsistent":
                    coverage_warnings.append((code, msg))
                else:
                    print(f"  [{code}] {msg}", file=sys.stderr)
                    all_issues.append(code)
                    valid = False
            if _proposal_text_7:
                for code, msg in _check_risk_criteria_alignment(
                    change, _proposal_text_7, _design_text_7,
                ):
                    if code == "criteria_no_risk_coverage":
                        coverage_warnings.append((code, msg))
                    else:
                        print(f"  [{code}] {msg}", file=sys.stderr)
                        all_issues.append(code)
                        valid = False
    except Exception as _e7:
        coverage_warnings.append((
            "v_identifier_consistency_check_failed",
            f"V-* 一致性校验异常: {_e7}",
        ))

    # 7.4d v1.4 (PR-B2): define-summary 三态 → 产物契约校验 (ERROR 级)
    try:
        _ds_path_b2 = os.path.join(CHANGES_DIR, change, "0-define", "define-summary.yaml")
        _design_path_b2 = os.path.join(CHANGES_DIR, change, "design.md")
        _proposal_path_b2 = os.path.join(CHANGES_DIR, change, "proposal.md")
        _design_text_b2 = ""
        _proposal_text_b2 = ""
        if os.path.isfile(_design_path_b2):
            with open(_design_path_b2, encoding="utf-8") as _f_b2:
                _design_text_b2 = _f_b2.read()
        if os.path.isfile(_proposal_path_b2):
            with open(_proposal_path_b2, encoding="utf-8") as _f_b2:
                _proposal_text_b2 = _f_b2.read()
        import glob as _glob_b2
        _scenario_files_b2 = _glob_b2.glob(
            os.path.join(CHANGES_DIR, change, "scenario-*.yaml")
        )
        for code, msg in _check_define_summary_propagation(
            _design_text_b2, _proposal_text_b2,
            list(_scenario_files_b2), _ds_path_b2,
        ):
            print(f"  [{code}] {msg}", file=sys.stderr)
            all_issues.append(code)
            valid = False
    except Exception as _e_b2:
        coverage_warnings.append((
            "define_summary_propagation_check_failed",
            f"三态契约校验异常: {_e_b2}",
        ))

    # 7.5 v3.10: scenario 覆盖度校验（warning 级, 不阻塞）
    if (_pg_gen_scenario is not None
            and yaml is not None
            and hasattr(_pg_gen_scenario, "check_scenario_coverage")):
        try:
            v_count = _pg_gen_scenario.parse_design_v_count(change)
            frontend_mentioned = _pg_gen_scenario.design_mentions_frontend(change)
            import glob as _glob
            for scenario_path in _glob.glob(
                os.path.join(
                    os.path.join(CHANGES_DIR, change),
                    "scenario-*.yaml",
                )
            ):
                try:
                    with open(scenario_path, encoding="utf-8") as _f:
                        scenario_doc = yaml.safe_load(_f)
                    coverage_issues = _pg_gen_scenario.check_scenario_coverage(
                        scenario_doc,
                        v_count=v_count,
                        design_mentions_frontend=frontend_mentioned,
                    )
                    fname = os.path.basename(scenario_path)
                    for code, msg in coverage_issues:
                        coverage_warnings.append((code, f"{fname}: {msg}"))
                except Exception as e:
                    coverage_warnings.append((
                        "scenario_coverage_check_failed",
                        f"{os.path.basename(scenario_path)}: {e}",
                    ))
        except Exception as e:
            coverage_warnings.append((
                "scenario_coverage_check_failed",
                f"coverage scan 异常: {e}",
            ))

    # 7.6 v1.0 (v6 hook 协议): scenario env-description 交叉校验（warning 级, 不阻塞）
    if yaml is not None:
        try:
            env_issues = _check_scenario_env_consistency(change, manifest)
            for code, msg in env_issues:
                coverage_warnings.append((code, msg))
        except Exception as e:
            coverage_warnings.append((
                "scenario_env_check_failed",
                f"env-description 交叉校验异常: {e}",
            ))

    # 8. v3.10: scenario 覆盖度 warning 打印（不阻塞）
    if coverage_warnings:
        print(f"\nWARN: {len(coverage_warnings)} scenario coverage warnings (不阻塞 validate):",
              file=sys.stderr)
        for code, msg in coverage_warnings:
            print(f"  [WARN:{code}] {msg}", file=sys.stderr)

    # 7. Result
    if all_issues:
        valid = False
        print(f"\nFAILED: {len(all_issues)} issue(s) found", file=sys.stderr)
    else:
        print("OK: all manifest checks passed")

    sys.exit(0 if valid else 1)


def main():
    if len(sys.argv) < 3:
        print(
            "Usage: python3 pg-validate-proposal.py {manifest|design-api|define-summary} <change>",
            file=sys.stderr,
        )
        sys.exit(1)

    subcmd = sys.argv[1]
    change = sys.argv[2]

    if subcmd == "manifest":
        cmd_manifest(change)
    elif subcmd == "design-api":
        cmd_design_api(change)
    elif subcmd == "define-summary":
        cmd_define_summary(change)
    else:
        print(f"未知子命令: {subcmd}", file=sys.stderr)
        print("支持: manifest, design-api, define-summary", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
