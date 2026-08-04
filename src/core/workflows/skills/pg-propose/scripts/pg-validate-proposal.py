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
    """建议 8: proposal R-* 风险与 design Verification Criteria 交叉校验.

    规则 1 (risk_criteria_missing): proposal R-* 描述中提到的 V-* ID
        不在 design.md Verification Criteria 表中 → ERROR
    规则 2 (criteria_no_risk_coverage): design Verification Criteria 表中的 V-*
        未被任何 proposal R-* 引用 → WARN

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

    if v_in_criteria:
        uncovered = sorted(v_in_criteria - set(v_in_risks.keys()))
        for vid in uncovered:
            issues.append((
                "criteria_no_risk_coverage",
                f"design.md V-{vid} 未被任何 proposal R-* 风险引用（可能漏风险）",
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

    # 7.4 v0.8.3: 简化版机械校验（替代 review-notes 主观自审）
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
            v_in_verify: set[tuple[str, str]] = set()
            for sec in tasks_sections:
                if sec.get("sub") != "verify":
                    continue
                for m in verify_v_re.finditer(sec.get("body", "")):
                    v_in_verify.add((m.group(1), m.group(2)))

            track_total: dict[str, int] = {}
            track_covered: dict[str, int] = {}
            for (track, _) in v_in_design:
                track_total[track] = track_total.get(track, 0) + 1
            for (track, _) in v_in_verify:
                if track in v_in_design:
                    track_covered[track] = track_covered.get(track, 0) + 1

            for track in sorted(track_total.keys()):
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

    # 7.4 v0.8.4: 简化版机械校验（替代 review-notes 主观自审）
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
            v_in_verify: set[tuple[str, str]] = set()
            for sec in tasks_sections:
                if sec.get("sub") != "verify":
                    continue
                for m in verify_v_re.finditer(sec.get("body", "")):
                    v_in_verify.add((m.group(1), m.group(2)))

            track_total: dict[str, int] = {}
            track_covered: dict[str, int] = {}
            for (track, _) in v_in_design:
                track_total[track] = track_total.get(track, 0) + 1
            for (track, _) in v_in_verify:
                if track in v_in_design:
                    track_covered[track] = track_covered.get(track, 0) + 1

            for track in sorted(track_total.keys()):
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
            "Usage: python3 pg-validate-proposal.py {manifest|design-api} <change>",
            file=sys.stderr,
        )
        sys.exit(1)

    subcmd = sys.argv[1]
    change = sys.argv[2]

    if subcmd == "manifest":
        cmd_manifest(change)
    elif subcmd == "design-api":
        cmd_design_api(change)
    else:
        print(f"未知子命令: {subcmd}", file=sys.stderr)
        print("支持: manifest, design-api", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
