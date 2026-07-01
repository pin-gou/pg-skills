"""Renderer — 加载 YAML 模板 → Jinja2 渲染。

模板目录结构：
  prompt-templates/
    base.yaml             # 所有 dispatch 的公共头部
    blocks/
      hooks.yaml          # invoke-hook 调用约定
      rollback.yaml       # [ROLLBACK CONTEXT] 块
      tasks.yaml          # tasks_preformatted 渲染
    test.yaml             # sub=test 完整模板
    dev.yaml
    verify.yaml
    gate.yaml
    fix.yaml
    fix-gate.yaml
    simple.yaml
    final-gate.yaml
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
    block_hooks = ""
    block_rollback = ""
    block_tasks = ""

    hooks_path = os.path.join(blocks_dir, "hooks.yaml")
    if os.path.isfile(hooks_path):
        hooks_data = _load_yaml(hooks_path)
        block_hooks = hooks_data.get("prompt", "")

    rollback_path = os.path.join(blocks_dir, "rollback.yaml")
    if os.path.isfile(rollback_path):
        rollback_data = _load_yaml(rollback_path)
        block_rollback = rollback_data.get("prompt", "")

    tasks_path = os.path.join(blocks_dir, "tasks.yaml")
    if os.path.isfile(tasks_path):
        tasks_data = _load_yaml(tasks_path)
        block_tasks = tasks_data.get("prompt", "")

    # 4. 合并 base + blocks + phase 模板
    sections = []
    if base.get("header"):
        sections.append(_safe_format(base["header"], ctx))
    sections.append(_safe_format(template_str, ctx))
    if block_hooks:
        sections.append(_safe_format(block_hooks, ctx))
    if block_tasks:
        sections.append(_safe_format(block_tasks, ctx))
    if block_rollback and ctx.get("rollback_context"):
        sections.append(_safe_format(block_rollback, ctx))

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