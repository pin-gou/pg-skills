"""Config — 从 project.yaml 解析模块/环境配置的纯函数。

旧 pg-build 的 _enrich_context_with_stage / _enrich_context_with_prompt_injection
等 5 步富化逻辑拆为独立纯函数，供 orchestrator._first_next() 调用。
"""

from __future__ import annotations

import os
from typing import Any


def load_project_config(root_dir: str) -> dict[str, Any]:
    """读取 .pg/project.yaml 返回 dict，文件不存在则返回空 dict。"""
    path = os.path.join(root_dir, ".pg", "project.yaml")
    if not os.path.isfile(path):
        return {}
    import yaml as _yaml
    with open(path, encoding="utf-8") as f:
        return _yaml.safe_load(f) or {}


def resolve_module_details(config: dict[str, Any], module_names: list[str]) -> str:
    """从 project.yaml.modules 解析模块详情文本。

    输出格式（多行 YAML 风格）：
      - module: backend
        - root: webvirt-backend
        - language: java
        - build: cd webvirt-backend && mvn clean install -DskipTests
        - lint: cd webvirt-backend && mvn checkstyle:check
        - test.unit: cd webvirt-backend && mvn test
    """
    modules = config.get("modules", {})
    lines: list[str] = []
    for name in module_names:
        mod = modules.get(name, {})
        lines.append(f"  - module: {name}")
        lines.append(f"    - root: {mod.get('root', '')}")
        if mod.get("language"):
            lines.append(f"    - language: {mod['language']}")
        if mod.get("build"):
            lines.append(f"    - build: {mod['build']}")
        if mod.get("lint"):
            lines.append(f"    - lint: {mod['lint']}")
        test = mod.get("test", {})
        if test.get("unit"):
            lines.append(f"    - test.unit: {test['unit']}")
        if test.get("integration"):
            val = test["integration"]
            cmd = val if isinstance(val, str) else val.get("cmd", "")
            if cmd:
                lines.append(f"    - test.integration: {cmd}")
    return "\n".join(lines)


def resolve_module_roots(config: dict[str, Any], module_names: list[str]) -> str:
    """解析模块根路径列表，格式化为 Python 列表字符串。"""
    modules = config.get("modules", {})
    roots: list[str] = []
    for name in module_names:
        mod = modules.get(name, {})
        root = mod.get("root", "")
        if root and root not in roots:
            roots.append(root)
    return str(roots)


def resolve_test_commands(
    config: dict[str, Any], module_names: list[str],
) -> str:
    """收集所有模块的 test.unit 命令，用 && 拼接。"""
    modules = config.get("modules", {})
    commands: list[str] = []
    for name in module_names:
        mod = modules.get(name, {})
        test = mod.get("test", {})
        val = test.get("unit")
        if isinstance(val, str):
            commands.append(val)
        elif isinstance(val, dict):
            cmd = val.get("cmd", "")
            if cmd:
                commands.append(cmd)
    return " && ".join(commands)


def resolve_module_languages(config: dict[str, Any], module_names: list[str]) -> tuple[str, ...]:
    """v2.6: 收集所有模块的 language 字段，去重保序。

    用于 review agent 按 language 自动派发 profile。
    """
    modules = config.get("modules", {})
    seen: set[str] = set()
    out: list[str] = []
    for name in module_names:
        mod = modules.get(name, {})
        lang = mod.get("language", "")
        if lang and lang not in seen:
            seen.add(lang)
            out.append(lang)
    return tuple(out)


def resolve_env_instances(config: dict[str, Any], env_name: str) -> str:
    """从 environments[{env_name}].roles[*].instances 渲染 YAML 文本。

    输出格式：
      backend:
        - name: backend-1
          host: localhost
          port: 9080
      frontend:
        - name: frontend-1
          host: localhost
          port: 3008

    role 顺序保留 environments.<env>.roles 的源码书写顺序（与
    `.pg/skills/src/runtime/bin/pg-run` 的 `_run_env_start_all()` 一致）。
    PyYAML 默认 sort_keys=True 会按字母序输出 dict key，导致 dispatch 与
    pg-run 看到相反的 role 顺序——必须显式 sort_keys=False。
    """
    env = config.get("environments", {}).get(env_name, {})
    roles = env.get("roles", {})
    if not roles:
        return ""
    import yaml as _yaml
    instance_map: dict[str, list[dict[str, Any]]] = {}
    for role_name, role_cfg in roles.items():
        insts = role_cfg.get("instances", [])
        if insts:
            instance_map[role_name] = [
                {k: v for k, v in inst.items() if k in ("name", "host", "port")}
                for inst in insts
            ]
    if not instance_map:
        return ""
    return _yaml.dump(
        instance_map,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    ).strip()


def resolve_hooks(config: dict[str, Any], env_name: str) -> str:
    """从 environments[{env_name}].roles[*].actions 渲染 YAML 文本。

    输出格式：
      backend:
        start:
          host: localhost
          script: .pg/hooks/role-backend-start.sh
          timeout_seconds: 300
        stop:
          ...

    role 顺序保留 environments.<env>.roles 的源码书写顺序（与
    `pg-run._run_env_start_all()` 一致）——必须显式 sort_keys=False。
    """
    env = config.get("environments", {}).get(env_name, {})
    roles = env.get("roles", {})
    if not roles:
        return ""
    import yaml as _yaml
    action_map: dict[str, dict[str, Any]] = {}
    for role_name, role_cfg in roles.items():
        actions = role_cfg.get("actions", {})
        if actions:
            simplified: dict[str, Any] = {}
            for act_name, act_cfg in actions.items():
                if isinstance(act_cfg, dict):
                    simplified[act_name] = {
                        k: v for k, v in act_cfg.items()
                        if k in ("host", "script", "timeout_seconds", "description")
                    }
            if simplified:
                action_map[role_name] = simplified
    if not action_map:
        return ""
    return _yaml.dump(
        action_map,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    ).strip()


def resolve_build_rules(
    config: dict[str, Any], phase: str,
) -> tuple[str, str]:
    """读取 build.injections.<phase>，返回 (prepend, append) 文本。

    从 project.yaml 的 build.injections 按 phase 直接取值，
    无需再 filter type / target_agent（key 已隐含作用域）。
    """
    rules = (config.get("build", {})
             .get("injections", {})
             .get(phase, []))
    prepend_parts: list[str] = []
    append_parts: list[str] = []

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        template = rule.get("template", "")
        if not template:
            continue
        position = rule.get("position", "append")
        if position == "prepend":
            prepend_parts.append(template)
        else:
            append_parts.append(template)

    prepend = "\n\n".join(prepend_parts)
    append = "\n\n".join(append_parts)
    return prepend, append