"""Renderer — 加载 YAML 模板 → Jinja2 渲染。

模板目录结构（v2.2）：
  prompt-templates/
    base.yaml             # 所有 dispatch 的公共头部（header + header_env）
    blocks/
      rollback.yaml       # [ROLLBACK CONTEXT] 块（仅子 pipeline 注入）
      tasks.yaml          # 任务清单 + 验证要求渲染
      sub_agent_contract.yaml  # v2.1 sub-agent 返回契约（强制 JSON schema）
    test.yaml             # sub=test 完整模板
    dev.yaml
    verify.yaml
    gate.yaml
    fix.yaml
    fix-gate.yaml
    simple.yaml
    final-gate.yaml

注：原 blocks/hooks.yaml 内容已整合到 base.yaml 的 header_env 段，
    由 renderer 根据 phase 条件（PHASES_WITH_ENV）注入。
"""

from __future__ import annotations

import os
from typing import Any


def _get_templates_dir() -> str:
    """返回 prompt-templates/ 目录路径。"""
    # 从本文件位置向上找到 scripts/ → 同级 prompt-templates/
    here = os.path.dirname(os.path.abspath(__file__))
    scripts = os.path.dirname(here)  # scripts/
    skills = os.path.dirname(scripts)  # pg-build-v2/
    return os.path.join(skills, "prompt-templates")


# v2.2: env.instances + env.hooks + 运行时环境操作指令 按 phase 条件注入
# WITH_ENV: dev/verify/fix/fix-gate —— 需要起停服务 / 看日志
# WITHOUT_ENV: test/gate/simple/final-gate —— 不直接操作服务
#   - test 阶段: 写测试代码 + 跑 mvn test, 不需要手动起停服务（由编排器 hook 管理）
#   - gate: 审查 evidence, 不需要操作服务
#   - simple: 只跑预定 commands
#   - final-gate: 聚合 gate assessment, 不需要操作服务
PHASES_WITH_ENV: frozenset[str] = frozenset({"dev", "verify", "fix", "fix-gate"})
PHASES_WITHOUT_ENV: frozenset[str] = frozenset({"test", "gate", "simple", "final-gate"})


def _load_yaml(path: str) -> dict[str, Any]:
    import yaml as _yaml
    with open(path, encoding="utf-8") as f:
        return _yaml.safe_load(f) or {}


def _safe_format(text: str, ctx: dict[str, Any]) -> str:
    """安全 format：缺失 key 静默替换为空字符串。"""
    if "{" not in text:
        return text
    try:
        return text.format(**ctx)
    except KeyError:
        # fallback：逐段替换缺失 key
        import re
        def _replacer(m):
            key = m.group(1)
            val = ctx.get(key, "")
            return str(val)
        return re.sub(r"\{(\w+)\}", _replacer, text)


def render_dispatch(
    phase: str,
    ctx: dict[str, Any],
    templates_dir: str | None = None,
) -> str:
    """渲染一个 phase 的 dispatch 模板。

    Args:
        phase: sub=test|dev|verify|gate|fix|fix-gate|simple|final-gate
        ctx: 上下文（含 track 配置、stage 配置等）
        templates_dir: 模板目录路径（默认从位置推断）

    Returns:
        渲染后的完整 prompt 字符串

    Raises:
        FileNotFoundError: 模板文件不存在
        ValueError: phase 不支持
    """
    td = templates_dir or _get_templates_dir()

    # 1. 加载 base.yaml
    base_path = os.path.join(td, "base.yaml")
    if not os.path.isfile(base_path):
        base = {}
    else:
        base = _load_yaml(base_path)

    # 2. 加载 phase 模板
    phase_path = os.path.join(td, f"{phase}.yaml")
    if not os.path.isfile(phase_path):
        raise FileNotFoundError(f"phase 模板不存在: {phase_path}")

    phase_tpl = _load_yaml(phase_path)
    template_str = phase_tpl.get("prompt", "") or ""

    # 3. 加载 blocks
    blocks_dir = os.path.join(td, "blocks")
    block_rollback = ""
    block_tasks = ""
    block_contract = ""

    # 注：v2.2 起 blocks/hooks.yaml 内容已整合到 base.yaml 的 header_env 段，
    # 由 renderer 根据 phase 条件注入，不再单独加载。

    rollback_path = os.path.join(blocks_dir, "rollback.yaml")
    if os.path.isfile(rollback_path):
        rollback_data = _load_yaml(rollback_path)
        block_rollback = rollback_data.get("prompt", "")

    tasks_path = os.path.join(blocks_dir, "tasks.yaml")
    if os.path.isfile(tasks_path):
        tasks_data = _load_yaml(tasks_path)
        block_tasks = tasks_data.get("prompt", "")

    # v2.1 新增：sub-agent 返回契约块（强制 JSON schema）
    contract_path = os.path.join(blocks_dir, "sub_agent_contract.yaml")
    if os.path.isfile(contract_path):
        contract_data = _load_yaml(contract_path)
        block_contract = contract_data.get("block", "")

    # v2.2: 按 phase 决定是否注入 env 块
    #  - PHASES_WITH_ENV (dev/verify/fix/fix-gate): 注入 header_env（紧跟 Stage 配置，含
    #    env_instances + env_hooks + 运行时环境操作指令 + ROLE/INSTANCE 来源解释）
    #  - PHASES_WITHOUT_ENV (test/gate/simple/final-gate): 跳过 header_env
    inject_env = phase in PHASES_WITH_ENV

    # 4. 合并 base + blocks + phase 模板
    sections = []
    if base.get("header"):
        sections.append(_safe_format(base["header"], ctx))
    # v2.2: 运行时环境操作指令紧跟 Stage 配置（仅 WITH_ENV phase 注入）
    if inject_env and base.get("header_env"):
        sections.append(_safe_format(base["header_env"], ctx))
    sections.append(_safe_format(template_str, ctx))
    if block_tasks:
        sections.append(_safe_format(block_tasks, ctx))
    if block_rollback and ctx.get("rollback_context"):
        sections.append(_safe_format(block_rollback, ctx))
    # v2.1: 契约块始终在最后，强调返回值约束
    if block_contract:
        sections.append(_safe_format(block_contract, ctx))

    return "\n\n---\n\n".join(sections)


def render_dispatch_file(
    change_root: str,
    track: str,
    phase: str,
    ctx: dict[str, Any],
    cycle: int = 1,
    templates_dir: str | None = None,
    output_dir: str | None = None,
    dispatch_seq: str = "",
) -> str:
    """渲染 dispatch 并写入文件。

    Args:
        change_root: change 根目录
        track: track id
        phase: phase name
        ctx: 上下文
        cycle: fix/gate-fix cycle number
        templates_dir: 模板目录
        output_dir: 输出目录（默认 change_root/2-build/）
        dispatch_seq: 3 位零填充全局 seq 前缀（如 "001"）,
                      空字符串时无前缀（向后兼容）

    Returns:
        写入的文件路径
    """
    content = render_dispatch(phase, ctx, templates_dir)

    od = output_dir or os.path.join(change_root, "2-build")
    os.makedirs(od, exist_ok=True)

    prefix = f"{dispatch_seq}-" if dispatch_seq else ""
    filename = f"{prefix}{track}-{phase}-dispatch"
    if cycle > 1:
        filename += f"-{cycle}"
    filename += ".md"

    filepath = os.path.join(od, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath