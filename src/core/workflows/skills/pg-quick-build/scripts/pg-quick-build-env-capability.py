#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pg-quick-build env-capability 判定器。

输入:
    - V-* 验证项列表（来自 design.verification）
    - env-description 6 段资源拓扑（来自 describe_env hook）

输出:
    - verifiable_v: 在目标 env 可达、可执行的 V-* id 列表
    - unverifiable_v: 因资源缺失 / 状态异常 / 引用未声明资源而不可达的 V-* id 列表
    - issues: 每条 unverifiable V-* 的具体原因

设计原则（与 pg-propose v0.9.0 同步）:
    - 资源命名引用 env-description.infra_services[].instances[].id 等具体 ID
    - 禁止以"环境未就绪" / "OSS 未配置"为兜底措辞
    - 纯函数: 无 I/O、无副作用
"""

from __future__ import print_function

import re


# env-description 6 段 SSOT 字段名（来自 src/runtime/spec/env-description.schema.json）
ENV_DESCRIPTION_SECTIONS = (
    "infra_services",
    "business_systems",
    "data_resources",
    "config_resources",
    "runtime_environment",
    "external_dependencies",
)

# 可达性的状态白名单（state.status 取值见 env-description.schema.json）
REACHABLE_STATES = frozenset({"ready", "configured", "seeded", "running", "available"})

# 资源引用提取正则：
#   - infra_services[name=db].instances[0].id
#   - data_resources[name=xxx].state.status
#   - business_systems[name=svc].endpoints[0].url
RESOURCE_REF_PATTERN = re.compile(
    r"(?P<section>infra_services|business_systems|data_resources|"
    r"config_resources|runtime_environment|external_dependencies)"
    r"\[name=(?P<name>[^\]]+)\]"
)


def _iter_resources(env_description):
    """遍历 env-description 顶层各段，返回 (section_name, resource_name, resource_dict) 元组。"""
    for section in ENV_DESCRIPTION_SECTIONS:
        bucket = env_description.get(section) or {}
        if not isinstance(bucket, dict):
            continue
        for name, resource in bucket.items():
            yield section, name, resource if isinstance(resource, dict) else {}


def _resolve_resource(env_description, section, name):
    """按 (section, name) 在 env-description 中查找资源。"""
    if not isinstance(env_description, dict):
        return None
    bucket = env_description.get(section) or {}
    if not isinstance(bucket, dict):
        return None
    resource = bucket.get(name)
    return resource if isinstance(resource, dict) else None


def _check_resource_reachable(resource):
    """判定单个资源是否可达。state.status 缺省视为 unknown（不可达）。"""
    if not resource:
        return False, "resource_not_found"
    state = resource.get("state") or {}
    status = state.get("status") if isinstance(state, dict) else None
    if status is None:
        return False, "state_unknown"
    if status in REACHABLE_STATES:
        return True, None
    return False, "state_" + str(status)


def _extract_resource_refs(verification_item):
    """从 V-* check / evidence 字段中提取资源引用列表。"""
    refs = []
    for field in ("check", "evidence"):
        text = verification_item.get(field) or ""
        if not isinstance(text, str):
            continue
        for match in RESOURCE_REF_PATTERN.finditer(text):
            refs.append((match.group("section"), match.group("name")))
    return refs


def evaluate(env_description, verifications):
    """
    评估 V-* 列表在目标 env 的可达性。

    Args:
        env_description: dict，env-description 6 段资源拓扑（可为空 dict 表示跳过探测）
        verifications: list[dict]，design.verification 列表

    Returns:
        dict，格式:
            {
                "verifiable_v": ["V-1", ...],
                "unverifiable_v": ["V-2", ...],
                "issues": [
                    {"v_id": "V-2", "reason": "...", "resource_ref": "..."},
                    ...
                ],
            }
    """
    env_description = env_description or {}
    verifications = verifications or []

    verifiable = []
    unverifiable = []
    issues = []

    for v in verifications:
        if not isinstance(v, dict):
            continue
        v_id = v.get("id")
        if not v_id:
            continue

        refs = _extract_resource_refs(v)
        if not refs:
            # V-* 未引用任何 env-description 资源 ID，视为可达
            # （例如纯单元测试 / 静态分析类验证）
            verifiable.append(v_id)
            continue

        # 检查每条资源引用：全部可达才算 verifiable
        blocked = []
        for section, name in refs:
            resource = _resolve_resource(env_description, section, name)
            ok, reason = _check_resource_reachable(resource)
            if not ok:
                blocked.append({
                    "section": section,
                    "name": name,
                    "reason": reason or "unknown",
                })

        if blocked:
            unverifiable.append(v_id)
            for b in blocked:
                issues.append({
                    "v_id": v_id,
                    "reason": b["reason"],
                    "resource_ref": "{section}[name={name}]".format(**b),
                })
        else:
            verifiable.append(v_id)

    return {
        "verifiable_v": verifiable,
        "unverifiable_v": unverifiable,
        "issues": issues,
    }


def filter_covers_v(tasks, verifiable_v):
    """
    从 tasks 中过滤 covers_v 字段，只保留 verifiable_v 子集。

    Args:
        tasks: list[dict]，in-memory tasks 列表
        verifiable_v: list[str]，可达 V-* id 列表

    Returns:
        new_tasks: list[dict]，过滤后的 tasks 副本（原列表不变）
    """
    verifiable_set = frozenset(verifiable_v or ())
    new_tasks = []
    for task in tasks or ():
        if not isinstance(task, dict):
            new_tasks.append(task)
            continue
        new_task = dict(task)
        if task.get("sub") == "verify":
            covers = task.get("covers_v") or []
            new_task["covers_v"] = [v for v in covers if v in verifiable_set]
        new_tasks.append(new_task)
    return new_tasks


if __name__ == "__main__":
    # 简易 CLI 入口：stdin 读 JSON，stdout 输出 JSON
    import json
    import sys

    payload = json.load(sys.stdin)
    result = evaluate(payload.get("env_description"), payload.get("verifications"))
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)