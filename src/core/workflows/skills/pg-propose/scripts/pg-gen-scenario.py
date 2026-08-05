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

v0.9.0 (历史): 新增 `--env-summary` 参数，写入 `_meta.env_constraint` 字段。
v1.0.0 (v6 hook 协议): **删除** `--env-summary` 与 `_meta.env_constraint`.
  取代方案: env-description.yaml 由 describe_env 脚本生成, LLM 在填充 scenario
  given 时直接引用 .pg/changes/<id>/env-description.yaml 中的具体资源路径.

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


def _extract_env_resources(env_desc_path: str) -> dict | None:
    """建议 18: 从 env-description.yaml 提取关键资源引用摘要.

    非侵入设计 (符合 v1.0.0 哲学): 摘要写入 skeleton _meta.env_resources,
    LLM 填充 given 时直接引用, 而不向 given 段注入 (避免破坏 placeholder 校验).

    Returns:
        {
            "data_resources": [{"name", "sample_ids": [...]}],
            "infra_services": [{"name", "endpoints": [...]}],
            "config_resources": [{"name", "location"}],
            "business_systems": [{"name", "endpoints": [...]}],
        }
        or None if file missing/invalid.
    """
    if not env_desc_path or not os.path.isfile(env_desc_path):
        return None
    try:
        with open(env_desc_path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None

    result: dict = {
        "data_resources": [],
        "infra_services": [],
        "config_resources": [],
        "business_systems": [],
    }

    for dr in doc.get("data_resources") or []:
        if not isinstance(dr, dict):
            continue
        sample_ids = [
            s.get("id") for s in (dr.get("sample") or [])
            if isinstance(s, dict) and s.get("id")
        ]
        if sample_ids:
            result["data_resources"].append({
                "name": dr.get("name"),
                "sample_ids": sample_ids[:5],
            })

    for svc in doc.get("infra_services") or []:
        if not isinstance(svc, dict):
            continue
        endpoints = [
            inst.get("endpoint")
            for inst in (svc.get("instances") or [])
            if isinstance(inst, dict) and inst.get("endpoint")
        ]
        if endpoints:
            result["infra_services"].append({
                "name": svc.get("name"),
                "endpoints": endpoints[:3],
            })

    for cr in doc.get("config_resources") or []:
        if isinstance(cr, dict) and cr.get("name") and cr.get("location"):
            result["config_resources"].append({
                "name": cr.get("name"),
                "location": cr.get("location"),
            })

    for bs in doc.get("business_systems") or []:
        if not isinstance(bs, dict):
            continue
        endpoints = [
            ep for ep in (bs.get("endpoints") or []) if isinstance(ep, str)
        ]
        if endpoints:
            result["business_systems"].append({
                "name": bs.get("name"),
                "endpoints": endpoints[:3],
            })

    has_content = any(result[k] for k in result)
    return result if has_content else None


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


def _build_api_skeleton_template(idx: int, covers_placeholder: list | None = None) -> dict:
    """v3.10: 单个 API 类型 scenario skeleton 模板.

    idx 用于生成不同 scenario_id 占位（unique）。
    covers_placeholder: 写入 'covers' 字段, LLM 必填替换为真实 V-* ID.

    v1.1.0: given/when/then 中如需引用 sandbox IP / 端口 / 账号,
    必须使用 {env.<段>.<name>.<field>} 占位引用 env-description.yaml 资源,
    pg-validate-proposal.py 校验时会拦截硬编码 IPv4 / ssh://user@host / port 字面.
    """
    return {
        "scenario_id": f"S-<unique-name-{idx}>",
        "critical": True,
        "description": "一句话描述此 Scenario 验证目标（LLM 必填）",
        "given": [
            "# REQUIRED: 前置条件；如引用 sandbox IP/端口/账号，必须用 {env.infra_services[name=<n>].instances[0].endpoint}",
            "<前置条件 1>",
            "<前置条件 2>",
        ],
        "covers": covers_placeholder if covers_placeholder is not None else [
            "<V-xxx-N>",
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
    }


def _build_browser_skeleton_template(idx: int, covers_placeholder: list | None = None) -> dict:
    """v3.10: 单个 browser 类型 scenario skeleton 模板.

    v1.1.0: given 段如需引用 frontend URL / 账号，同样必须用 {env.<段>.<name>.<field>}.
    """
    return {
        "scenario_id": f"S-<unique-name-{idx}-browser>",
        "critical": False,
        "description": "一句话描述此 Browser Scenario 验证目标（LLM 必填）",
        "given": [
            "# REQUIRED: 前置条件；URL/账号引用见 {env.*} 占位规范",
            "<前置条件 1>",
        ],
        "covers": covers_placeholder if covers_placeholder is not None else [
            "<V-frontend-N>",
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
    }


def _compute_target_scenario_count(v_count: int) -> int:
    """v3.10: 派生 skeleton 数量.

    公式: max(3, ceil(v_count * 0.8)), 上限软化为 7.
    v_count <= 0 → 默认 3.
    """
    if v_count <= 0:
        return 3
    target = (v_count * 8 + 9) // 10  # ceil(v_count * 0.8)
    target = max(3, target)
    target = min(7, target)
    return target


def _build_skeleton_yaml(
    change: str, track_id: str, v_count: int = 0, design_mentions_frontend: bool = False,
    env_resources: dict | None = None,
) -> dict:
    """构造 scenario-<track-id>.yaml skeleton —— LLM 在阶段三自审时填充。

    v3.7: 占位符可由 check_scenario_placeholders 检测（每个字段含一个
    明显的占位符字符串，LLM 编辑后不能残留）。

    v3.9: 生成两个 skeleton scenario：一个 type=api（向后兼容），
    一个 type=browser（浏览器交互场景，使用 Chrome DevTools MCP 工具）。

    v3.10: 数量按 design.md V-* 数动态派生 (max(3, ceil(V*0.8)), 上限 7);
    强制含 ≥1 个 type=browser scenario 当 design 含 frontend V-* 时.

    建议 18: env_resources (来自 env-description.yaml) 写入 _meta.env_resources,
    LLM 填充 given 时引用; 不向 given 注入, 避免破坏 placeholder 校验。
    """
    target_count = _compute_target_scenario_count(v_count)
    covers_placeholder = ["<V-xxx-N>"]

    scenarios: list[dict] = []
    # 生成 API scenarios: target_count - 1 个 (browser 占一位)
    api_count = max(1, target_count - 1)
    for i in range(1, api_count + 1):
        scenarios.append(_build_api_skeleton_template(i, covers_placeholder))

    # 当 design 含 frontend V-* 时强制加 browser scenario
    if design_mentions_frontend:
        scenarios.append(_build_browser_skeleton_template(api_count + 1, covers_placeholder))

    return {
        "scenarios": scenarios,
        "_meta": {
            "_comment": (
                "scenario-<track>.yaml 由 pg-gen-scenario.py 生成的 skeleton, "
                "LLM 必填。scenario_id / given / when / then / and / evidence "
                "是必填段, critical / description 必填, covers 是 v3.10 推荐必填段。"
                "_meta 段最终会被 pg-build scenario-execute agent 忽略。"
                "v3.9: when[].type 可选, 默认 api; type=browser 时需填写 browser action 字段。"
                "v3.10: 数量按 design V-* 数动态派生, 上限 7。"
                "若不需要 browser 场景可删除最后一个 scenario；"
                "若 V-* 不足, 建议补充 scenario 而非合并现有 scenario。"
                "v1.0 (v6 hook 协议): scenario given 直接引用 .pg/changes/<id>/env-description.yaml 中的资源路径, "
                "不再使用 _meta.env_constraint 字段."
                "v1.1.0 (硬约束): given/when/then 中禁止硬编码 IPv4 / ssh://user@host / port 字面, "
                "必须用 {env.<段>.<name>.<字段路径>} 占位引用 env-description.yaml 中的真实资源。"
                "pg-validate-proposal.py 校验规则 scenario_given_hardcoded_endpoint 拦截违规。"
            ),
            "change": change,
            "track_id": track_id,
            "schema_version": "v3.10",
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


# v3.10: scenario 覆盖度校验 — warning 级 (不阻塞 validate)
_V_COUNT_RE = __import__("re").compile(r"\|\s*(V-[A-Za-z0-9_-]+-[A-Za-z0-9_-]+)\s*\|")


def parse_design_v_count(change: str) -> int:
    """v3.10: 从 .pg/changes/<change>/design.md 的 ## Verification Criteria 段
    数 V-* 行.

    匹配模式: 表格行 | V-xxx-N | ... |
    仅在 design.md 存在且含 Verification Criteria 段时返回 > 0.
    """
    design_path = os.path.join(CHANGES_DIR, change, "design.md")
    if not os.path.isfile(design_path):
        return 0
    try:
        with open(design_path, encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return 0
    if "## Verification Criteria" not in content:
        return 0
    section = content.split("## Verification Criteria", 1)[1]
    ids = set()
    for line in section.splitlines():
        m = _V_COUNT_RE.search(line)
        if m:
            ids.add(m.group(1))
    return len(ids)


def design_mentions_frontend(change: str) -> bool:
    """v3.10: design.md Verification Criteria 中是否含 V-frontend-* 引用."""
    design_path = os.path.join(CHANGES_DIR, change, "design.md")
    if not os.path.isfile(design_path):
        return False
    try:
        with open(design_path, encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return False
    if "## Verification Criteria" not in content:
        return False
    section = content.split("## Verification Criteria", 1)[1]
    return bool(_V_COUNT_RE.search(section) and "V-frontend-" in section)


def _classify_scenario_dimension(sc: dict) -> set[str]:
    """v3.10: 把单个 scenario 分类到 5 个维度 (happy / negative / permission / cross-module / ui-smoke).

    启发式:
      - happy: when 含 expect_status 200/201 且无 type=browser
      - negative: when 含 expect_status >= 400 或描述含'失败/不存在/非法'关键字
      - permission: 描述含'权限/跨租户/跨项目/RBAC/越权'
      - cross-module: when 步骤数 >= 3 (跨多个 API), 或 description 含 '联调/跨模块/端到端'
      - ui-smoke: 任一 when step type=browser
    """
    dims: set[str] = set()
    description = str(sc.get("description", ""))
    whens = sc.get("when") or []
    has_browser = any(
        isinstance(w, dict) and w.get("type", "api") == "browser" for w in whens
    )
    if has_browser:
        dims.add("ui-smoke")

    # expect_status 收集
    api_statuses = [
        w.get("expect_status") for w in whens
        if isinstance(w, dict) and w.get("type", "api") == "api"
    ]
    if not api_statuses and not has_browser:
        api_statuses = [
            w.get("expect_status") for w in whens
            if isinstance(w, dict) and w.get("expect_status") is not None
        ]
    neg_keywords = ("失败", "不存在", "非法", "错误", "missing", "fail", "error", "invalid")
    perm_keywords = ("权限", "跨租户", "跨项目", "RBAC", "越权", "permission")
    cross_keywords = ("联调", "跨模块", "端到端", "cross-module", "end-to-end")

    if any(isinstance(s, int) and s >= 400 for s in api_statuses) or any(
        k in description.lower() for k in neg_keywords
    ):
        dims.add("negative")
    if any(isinstance(s, int) and 200 <= s < 300 for s in api_statuses) and not has_browser:
        dims.add("happy")
    if any(k in description for k in perm_keywords) or any(k in description.lower() for k in ("permission",)):
        dims.add("permission")
    if len(whens) >= 3 or any(k in description for k in cross_keywords) or any(
        k in description.lower() for k in ("cross-module", "end-to-end")
    ):
        dims.add("cross-module")
    return dims


def check_scenario_coverage(
    scenario_doc: dict, v_count: int = 0, design_mentions_frontend: bool = False,
) -> list[tuple[str, str]]:
    """v3.10: 校验 scenario 集合覆盖度 (warning 级, 不阻塞).

    4 类 issue:
      - scenario_coverage_dimension_missing: 5 维度未覆盖够 3 项
      - scenario_coverage_count_below_min: len < max(2, ceil(V*0.8)), 含未覆盖 V-* 列表
      - scenario_coverage_type_imbalance: design 含 frontend 但缺 api 或 browser
      - scenario_coverage_covers_unset: Scenario 缺 covers 字段
      - scenario_coverage_critical_overflow: critical=true 超过 3 个

    Args:
        scenario_doc: 已解析的 scenario YAML dict
        v_count: design.md 中 V-* 总数 (由 parse_design_v_count 返回)
        design_mentions_frontend: design.md 是否含 V-frontend-*
    """
    issues: list[tuple[str, str]] = []
    if not isinstance(scenario_doc, dict):
        return issues
    scenarios = scenario_doc.get("scenarios") or []
    if not isinstance(scenarios, list) or not scenarios:
        return issues  # placeholder 校验负责 catch 空数组

    # 维度聚合
    all_dims: set[str] = set()
    for sc in scenarios:
        if isinstance(sc, dict):
            all_dims.update(_classify_scenario_dimension(sc))

    if len(all_dims) < 3:
        issues.append((
            "scenario_coverage_dimension_missing",
            f"scenarios 集合仅覆盖 {len(all_dims)} 个维度 {sorted(all_dims)}, "
            f"建议至少 3 个 (happy/negative/permission/cross-module/ui-smoke)",
        ))

    # 数量下限: max(2, min(7, ceil(V*0.8))) — 与 SKILL 附录 C 对齐:
    # "建议数 = max(3, ceil(V-* × 0.8)); 上限软化为 7; 下限 2"
    # 旧实现未 cap 上限, V-* 较多时 (如 20) 建议下限 16 > 上限 7, 产生不可满足的 WARN
    if v_count > 0:
        min_count = min(7, max(2, (v_count * 8 + 9) // 10))
        if len(scenarios) < min_count:
            issues.append((
                "scenario_coverage_count_below_min",
                f"scenarios 数={len(scenarios)} < 建议下限 {min_count} (由 V-*={v_count} 派生, 上限 7). "
                f"建议补充覆盖更多 V-* 验证项, 未覆盖 V-*: 数量提示 — 重新审视 V-* 列表",
            ))

    # 类型维度
    if design_mentions_frontend and scenarios:
        has_api = False
        has_browser = False
        for sc in scenarios:
            if not isinstance(sc, dict):
                continue
            for w in sc.get("when") or []:
                if not isinstance(w, dict):
                    continue
                t = w.get("type", "api")
                if t == "browser":
                    has_browser = True
                else:
                    has_api = True
        if not has_api or not has_browser:
            issues.append((
                "scenario_coverage_type_imbalance",
                f"design.md 含 V-frontend-*, scenario 集合需 ≥1 API + ≥1 browser. "
                f"当前 api={has_api}, browser={has_browser}",
            ))

    # covers 字段
    for idx, sc in enumerate(scenarios):
        if not isinstance(sc, dict):
            continue
        if "covers" not in sc:
            issues.append((
                "scenario_coverage_covers_unset",
                f"scenarios[{idx}] 缺 covers 字段 (v3.10 推荐), "
                f"应在 each scenario 中引用 design.md 的 V-* ID",
            ))

    # critical 过多
    critical_count = sum(
        1 for sc in scenarios
        if isinstance(sc, dict) and sc.get("critical") is True
    )
    if critical_count > 3:
        issues.append((
            "scenario_coverage_critical_overflow",
            f"critical=true 数={critical_count} 超过建议 3 个, "
            f"过多会放大 execute 阶段 escalate 噪音",
        ))

    return issues


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 pg-gen-scenario.py <change>", file=sys.stderr)
        sys.exit(1)

    change = sys.argv[1]

    decisions = _read_scenario_decisions(change)

    if decisions is None:
        print(
            f"ERROR: scenario_tracks_decision 段缺失, 必须先跑:\n"
            f"  python3 .pg/skills/src/core/workflows/skills/pg-propose/scripts/pg-gen-tasks-skeleton.py "
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
    # v3.10: 先解析 design.md 的 V-* 数与 frontend 提及, 作为 skeleton 派生输入
    v_count = parse_design_v_count(change)
    frontend_mentioned = design_mentions_frontend(change)
    for track_id, decision in enabled_tracks.items():
        filename = f"scenario-{track_id}.yaml"
        scenario_path = os.path.join(CHANGES_DIR, change, filename)
        os.makedirs(os.path.dirname(scenario_path), exist_ok=True)
        skeleton = _build_skeleton_yaml(
            change, track_id, v_count=v_count, design_mentions_frontend=frontend_mentioned,
        )
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
        "v_count": v_count,
        "design_mentions_frontend": frontend_mentioned,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()