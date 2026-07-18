#!/usr/bin/env python3
"""pg-gen-scenario.py — Generate per-track scenario-<track>.yaml skeletons.

Usage:
    python3 pg-gen-scenario.py <change>

Reads `.pg/changes/<change>/1-propose-review/on-conditions-eval.md` to find
`scenario_tracks_decision` segment (SSOT written by `pg-gen-tasks-skeleton.py`).

Behavior:
  - For each enabled scenario track: write `scenario-<track-id>.yaml` skeleton
    (LLM 必须在阶段三自审时填充 Scenario 内容)
  - No enabled tracks → no-op (do NOT write any scenario files)
  - decision missing → emit error: must run `pg-gen-tasks-skeleton.py` first

This script is pure-function (zero side effects beyond writing scenario files).

v3.7: 新增 `check_scenario_placeholders()` / `check_scenario_file()` 工具函数，
供 `pg-validate-proposal.py` 调用以校验 LLM 是否已替换所有占位符。
"""

from __future__ import annotations

import json
import os
import sys

try:
    import yaml
except ImportError:
    print('{"error": "PyYAML is required. Install with: pip install pyyaml"}',
          file=sys.stderr)
    sys.exit(1)

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

from pg_pipeline_common import CHANGES_DIR

_DECISION_MARKER = "## scenario_tracks_decision (v3.6)"


def _read_scenario_decisions(change: str) -> dict | None:
    """从 on-conditions-eval.md 读取 scenario_tracks_decision 段 (多 track).

    Returns:
        dict of {track_id: {enabled: bool, mode: str, reason: str}}
        或 None (eval.md 不存在 / 段缺失)
    """
    eval_path = os.path.join(
        CHANGES_DIR, change, "1-propose-review", "on-conditions-eval.md"
    )
    if not os.path.isfile(eval_path):
        return None
    try:
        with open(eval_path, encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return None

    if _DECISION_MARKER not in content:
        return None
    section = content.split(_DECISION_MARKER, 1)[1]
    section = section.split("\n## ", 1)[0]

    decisions: dict[str, dict] = {}
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        parts = [p.strip().strip("*") for p in line.split("|")]
        if len(parts) < 5:
            continue
        tid = parts[1].strip()
        if not tid or tid == "track_id":
            continue
        decisions[tid] = {
            "enabled": parts[2].strip().lower() == "true",
            "mode": parts[3].strip(),
            "reason": parts[4].strip(),
        }
    return decisions if decisions else None


def _build_skeleton_yaml(change: str, track_id: str) -> dict:
    """构造 scenario-<track-id>.yaml skeleton —— LLM 在阶段三自审时填充。

    v3.7: 占位符可由 check_scenario_placeholders 检测（每个字段含一个
    明显的占位符字符串，LLM 编辑后不能残留）。

    v3.9: 生成两个 skeleton scenario：一个 type=api（向后兼容），
    一个 type=browser（浏览器交互场景，使用 Chrome DevTools MCP 工具）。
    LLM 按实际需求保留并填充：若不需要 browser 场景可删除第二个 scenario；
    若不需要 API 场景可删除第一个并调整第二个的 type。
    """
    return {
        "scenarios": [
            {
                "scenario_id": "S-<unique-name>",
                "critical": True,
                "description": "一句话描述此 Scenario 验证目标（LLM 必填）",
                "given": [
                    "<前置条件 1>",
                    "<前置条件 2>",
                ],
                "when": [
                    {
                        "name": "<动作名>",
                        "type": "api",
                        "method": "GET",
                        "url": "/api/.../...",
                        "expect_status": 200,
                    },
                ],
                "then": [
                    "status_code == 200",
                    "response.<field> matches <regex>",
                ],
                "and": [
                    {"name": "<cleanup>", "action": "HTTP DELETE /api/.../.../..."},
                ],
                "evidence": [
                    "2-build/<report_seq>-<scenario_id>-evidence.json",
                ],
            },
            {
                "scenario_id": "S-<unique-name>-browser",
                "critical": False,
                "description": "一句话描述此 Browser Scenario 验证目标（LLM 必填）",
                "given": [
                    "<前置条件 1>",
                ],
                "when": [
                    {
                        "name": "导航到页面",
                        "type": "browser",
                        "action": "navigate",
                        "url": "/path/to/page",
                    },
                    {
                        "name": "点击按钮",
                        "type": "browser",
                        "action": "click",
                        "selector": "<CSS选择器>",
                    },
                    {
                        "name": "填写输入框",
                        "type": "browser",
                        "action": "fill",
                        "selector": "<CSS选择器>",
                        "value": "<输入值>",
                    },
                    {
                        "name": "截图验证",
                        "type": "browser",
                        "action": "screenshot",
                    },
                ],
                "then": [
                    "dom: <selector> exists",
                    "console: no errors",
                ],
                "and": [],
                "evidence": [
                    "2-build/<report_seq>-<scenario_id>-evidence.json",
                    "2-build/<report_seq>-<scenario_id>-screenshot.png",
                ],
            },
        ],
        "_meta": {
            "_comment": (
                "scenario-<track>.yaml 由 pg-gen-scenario.py 生成的 skeleton, "
                "LLM 必填。scenario_id / given / when / then / and / evidence "
                "是必填段, critical / description 必填, _meta 段最终会被 pg-build "
                "scenario-execute agent 忽略。"
                "v3.9: when[].type 可选, 默认 api; type=browser 时需填写 browser action 字段。"
                "若不需要 browser 场景可删除第二个 scenario。"
            ),
            "change": change,
            "track_id": track_id,
            "schema_version": "v3.9",
        },
    }


# v3.7: placeholder 校验协议 (详见 references/scenario-format.md)

_PLACEHOLDER_FIELDS = (
    "scenario_id",
    "description",
    "given",
    "when",
    "then",
    "and",
    "evidence",
)


def _iter_string_values(node):
    """Yield all leaf string values from a YAML node (recursive)."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _iter_string_values(v)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_string_values(item)


# v3.8: 运行时注入占位符 — evidence 字段中 LLM 替换 <scenario_id> 后，
# <report_seq> 由 pg-build 编排器在 dispatch 时注入，LLM 不应替换也不该被检测为占位符。
_RUNTIME_PLACEHOLDER_RE = None


def _runtime_placeholder_pattern() -> "_re.Pattern[str]":
    """延迟编译运行时占位符正则."""
    global _RUNTIME_PLACEHOLDER_RE
    import re as _re
    if _RUNTIME_PLACEHOLDER_RE is None:
        _RUNTIME_PLACEHOLDER_RE = _re.compile(r"<report_seq>")
    return _RUNTIME_PLACEHOLDER_RE


def _is_placeholder_string(value: str) -> bool:
    """检测字符串是否仍含 v3.7 skeleton 的占位符模式.

    识别模式:
      - "<...>" (尖括号占位符，含 <scenario_id> / <前置条件> 等)
      - "/.../" 路径中含三连点（如 /api/<scope>.../v3/...）
      - 以 "S-<" 起头的未替换 scenario_id
      - "（LLM 必填）" 注释占位符

    例外: <report_seq> 是运行时注入占位符（pg-build 编排器在 dispatch 时
    注入真实 seq），LLM 不应替换也不该被检测为占位符。
    """
    if not isinstance(value, str) or not value:
        return False
    import re as _re
    placeholders = [
        _re.compile(r"<[^>]+>"),  # <...>
        _re.compile(r"/[^/\s]*\.\.\.[^/\s]*/"),  # 路径中含 ...
        _re.compile(r"^S-<"),  # S-<unique-name>
        _re.compile(r"LLM\s*必填"),
    ]
    # 先剥除运行时占位符，再检测是否仍含 LLM 占位符
    stripped = _runtime_placeholder_pattern().sub("", value)
    return any(p.search(stripped) for p in placeholders)


def check_scenario_placeholders(scenario_doc: dict) -> list[tuple[str, str]]:
    """v3.7: 检测 scenario YAML 文档是否仍含占位符.

    Args:
        scenario_doc: 已解析的 scenario YAML dict (从 scenario-<track>.yaml 读入).

    Returns:
        List of (code, message) tuples. code 为 "scenario_placeholder_unfilled".
        文件完全填充则返回空列表。

    协议参见: references/scenario-format.md "placeholder 校验协议"段.
    """
    issues = []
    if not isinstance(scenario_doc, dict):
        return [("scenario_placeholder_unfilled", "scenario YAML 顶层必须是 object")]
    scenarios = scenario_doc.get("scenarios") or []
    if not isinstance(scenarios, list) or not scenarios:
        return [("scenario_placeholder_unfilled", "scenarios 字段必须是非空数组")]

    for idx, sc in enumerate(scenarios):
        if not isinstance(sc, dict):
            issues.append((
                "scenario_placeholder_unfilled",
                f"scenarios[{idx}] 必须是 object, 实际: {type(sc).__name__}"
            ))
            continue
        # per-field check
        sid = sc.get("scenario_id", "")
        if _is_placeholder_string(sid) or not sid:
            issues.append((
                "scenario_placeholder_unfilled",
                f"scenarios[{idx}].scenario_id 仍含占位符或为空: {sid!r}"
            ))
        desc = sc.get("description", "")
        if _is_placeholder_string(desc) or not desc:
            issues.append((
                "scenario_placeholder_unfilled",
                f"scenarios[{idx}].description 仍含占位符或为空: {desc!r}"
            ))
        # given/then/evidence: any placeholder in any item
        for field in ("given", "evidence"):
            items = sc.get(field) or []
            if not isinstance(items, list) or not items:
                issues.append((
                    "scenario_placeholder_unfilled",
                    f"scenarios[{idx}].{field} 必须是非空数组"
                ))
                continue
            for j, item in enumerate(items):
                if _is_placeholder_string(str(item)) or not str(item).strip():
                    issues.append((
                        "scenario_placeholder_unfilled",
                        f"scenarios[{idx}].{field}[{j}] 仍含占位符或为空: {item!r}"
                    ))
        # and: cleanup is optional (e.g., browser-only 场景如登录页测试无 cleanup 需求)
        # v3.9: 放宽 and 的强制要求——若所有 when step 都是 type=browser 则 and 可为空数组
        whens_for_and_check = sc.get("when") or []
        all_browser_steps = all(
            isinstance(w, dict) and w.get("type", "api") == "browser"
            for w in whens_for_and_check
        ) if whens_for_and_check else False
        and_items = sc.get("and") or []
        if not isinstance(and_items, list):
            issues.append((
                "scenario_placeholder_unfilled",
                f"scenarios[{idx}].and 必须是数组"
            ))
        elif and_items and not all_browser_steps:
            # API scenarios or mixed: 检查每个 cleanup 项的占位符
            for j, item in enumerate(and_items):
                if _is_placeholder_string(str(item)) or not str(item).strip():
                    issues.append((
                        "scenario_placeholder_unfilled",
                        f"scenarios[{idx}].and[{j}] 仍含占位符或为空: {item!r}"
                    ))
        # when: list of dicts with method/url/expect_status (type=api) or action/selector/value (type=browser)
        whens = sc.get("when") or []
        if not isinstance(whens, list) or not whens:
            issues.append((
                "scenario_placeholder_unfilled",
                f"scenarios[{idx}].when 必须是非空数组"
            ))
        else:
            for j, w in enumerate(whens):
                if not isinstance(w, dict):
                    continue
                step_type = w.get("type", "api")
                if step_type == "browser":
                    # browser step: check action/selector/value placeholders
                    action = w.get("action", "")
                    if _is_placeholder_string(action) or not action:
                        issues.append((
                            "scenario_placeholder_unfilled",
                            f"scenarios[{idx}].when[{j}].action 仍含占位符或为空: {action!r}"
                        ))
                    selector = w.get("selector", "")
                    if selector and _is_placeholder_string(selector):
                        issues.append((
                            "scenario_placeholder_unfilled",
                            f"scenarios[{idx}].when[{j}].selector 仍含占位符: {selector!r}"
                        ))
                    value = w.get("value", "")
                    if value and _is_placeholder_string(value):
                        issues.append((
                            "scenario_placeholder_unfilled",
                            f"scenarios[{idx}].when[{j}].value 仍含占位符: {value!r}"
                        ))
                    key = w.get("key", "")
                    if key and _is_placeholder_string(key):
                        issues.append((
                            "scenario_placeholder_unfilled",
                            f"scenarios[{idx}].when[{j}].key 仍含占位符: {key!r}"
                        ))
                    expression = w.get("expression", "")
                    if expression and _is_placeholder_string(expression):
                        issues.append((
                            "scenario_placeholder_unfilled",
                            f"scenarios[{idx}].when[{j}].expression 仍含占位符: {expression!r}"
                        ))
                    condition = w.get("condition", "")
                    if condition and _is_placeholder_string(condition):
                        issues.append((
                            "scenario_placeholder_unfilled",
                            f"scenarios[{idx}].when[{j}].condition 仍含占位符: {condition!r}"
                        ))
                else:
                    # api step: check url placeholder
                    url = w.get("url", "")
                    if _is_placeholder_string(url) or not url:
                        issues.append((
                            "scenario_placeholder_unfilled",
                            f"scenarios[{idx}].when[{j}].url 仍含占位符或为空: {url!r}"
                        ))
        # then: list of strings
        thens = sc.get("then") or []
        if not isinstance(thens, list) or not thens:
            issues.append((
                "scenario_placeholder_unfilled",
                f"scenarios[{idx}].then 必须是非空数组"
            ))
        else:
            for j, t in enumerate(thens):
                if _is_placeholder_string(str(t)) or not str(t).strip():
                    issues.append((
                        "scenario_placeholder_unfilled",
                        f"scenarios[{idx}].then[{j}] 仍含占位符或为空: {t!r}"
                    ))
    return issues


def check_scenario_file(filepath: str) -> list[tuple[str, str]]:
    """v3.7: 检查 scenario YAML 文件是否含占位符.

    Convenience wrapper around check_scenario_placeholders.
    Returns [] on read/parse errors (those are surfaced elsewhere).
    """
    try:
        with open(filepath, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except Exception as e:
        return [("scenario_placeholder_unfilled", f"无法解析文件: {e}")]
    if doc is None:
        return [("scenario_placeholder_unfilled", "scenario YAML 文件为空")]
    return check_scenario_placeholders(doc)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 pg-gen-scenario.py <change>", file=sys.stderr)
        sys.exit(1)

    change = sys.argv[1]
    decisions = _read_scenario_decisions(change)

    if decisions is None:
        print(
            f"ERROR: scenario_tracks_decision 段缺失, 必须先跑:\n"
            f"  python3 .opencode/skills/pg-propose/scripts/pg-gen-tasks-skeleton.py "
            f"--change {change} --scenario-decisions 'track1=true,track2=auto' --scenario-reason '...' ...",
            file=sys.stderr,
        )
        sys.exit(1)

    enabled_tracks = {tid: d for tid, d in decisions.items() if d["enabled"]}
    if not enabled_tracks:
        result = {
            "scenario_files_written": [],
            "scenario_tracks_enabled": False,
            "action": "skipped (no scenario track enabled)",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    written = []
    for track_id, decision in enabled_tracks.items():
        filename = f"scenario-{track_id}.yaml"
        scenario_path = os.path.join(CHANGES_DIR, change, filename)
        os.makedirs(os.path.dirname(scenario_path), exist_ok=True)
        skeleton = _build_skeleton_yaml(change, track_id)
        with open(scenario_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                skeleton, f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        written.append(filename)

    result = {
        "scenario_files_written": written,
        "scenario_tracks_enabled": True,
        "action": f"skeletons written for {len(written)} track(s): {', '.join(written)}",
        "reason": next(iter(enabled_tracks.values()))["reason"],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()