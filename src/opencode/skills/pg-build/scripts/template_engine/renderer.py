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
    skills = os.path.dirname(scripts)  # pg-build/
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
    """Load a YAML file that may contain Jinja2 template blocks.

    Handles two formats:
    1. YAML frontmatter (--- ... ---) + Jinja2 template body — extract frontmatter only
    2. Pure YAML with {placeholder} strings — safe_load works fine
    3. Mixed YAML + Jinja2 tags ({%%}/{{}}) — safe_load would fail on the Jinja2,
       so we fall back to a simple key: value regex extractor for the YAML frontmatter
       portion (everything before the first block scalar | or >).
    """
    import yaml as _yaml
    import re as _re
    with open(path, encoding="utf-8-sig") as f:
        raw = f.read()

    # Strip BOM already handled by utf-8-sig open
    stripped = raw.lstrip("\r\n")

    # Format 1: YAML frontmatter --- ... ---
    if stripped.startswith("---"):
        end = stripped.find("\n---", 3)
        if end >= 0:
            frontmatter = stripped[3:end].strip()
            if frontmatter:
                return _yaml.safe_load(frontmatter) or {}
            return {}

    # Format 2: standard YAML (no Jinja2 or Jinja2 only in scalar strings)
    # yaml.safe_load handles {placeholder} inside | blocks fine
    try:
        result = _yaml.safe_load(raw)
        if isinstance(result, dict):
            return result
        # If safe_load returns a string, it means the YAML parser stopped at
        # a Jinja2 tag and consumed everything after as a scalar string.
        # Fall through to frontmatter extraction.
    except Exception:
        pass

    # Format 3: Mixed YAML + Jinja2 — extract key: value pairs before the first
    # block scalar (| or >) or first indented line after a Jinja2 tag.
    # We do a simple regex scan for "key: value" lines in the first non-comment,
    # non-blank portion of the file.
    frontmatter_lines = []
    for line in raw.splitlines():
        ls = line.lstrip()
        # Stop at block scalar marker or indented content (prompt body)
        if ls.startswith("|") or ls.startswith(">"):
            break
        # Stop at first indented non-Jinja line after YAML keys (prompt body starts)
        if frontmatter_lines and ls and not ls.startswith("#") and not ls.startswith("{"):
            # Check if it looks like YAML continuation (indented under a key)
            if line.startswith(" ") or line.startswith("\t"):
                break
        if ls and not ls.startswith("#"):
            frontmatter_lines.append(line.rstrip())

    fm_text = "\n".join(frontmatter_lines)
    if fm_text.strip():
        try:
            return _yaml.safe_load(fm_text) or {}
        except Exception:
            pass

    return {}


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


# v2.6 新增：按 phase 推导 sub-agent 返回契约的策略占位符
# 让 sub_agent_contract.yaml 同一份模板渲染出与 phase 匹配的精简字段表
_CONTRACT_PHASE_POLICIES: dict[str, dict[str, str]] = {
    "test": {
        "status_matrix": "| phase=`test` | `completed` / `failed` |",
        "report_policy": "选填",
        "evidence_policy": "选填",
        "tasks_updated_policy": "**必填**（覆盖的 task_id 列表，可多次传或逗号分隔）",
    },
    "dev": {
        "status_matrix": "| phase=`dev` | `completed` / `failed` |",
        "report_policy": "选填",
        "evidence_policy": "选填",
        "tasks_updated_policy": "**必填**（实现的 task_id 列表，可多次传或逗号分隔）",
    },
    "verify": {
        "status_matrix": "| phase=`verify` | `completed` / `escalate` / `failed` |",
        "report_policy": "**必填**（报告文件绝对路径，文件必须已写盘）",
        "evidence_policy": "**至少 1 个**（证据文件绝对路径，可多次传）",
        "tasks_updated_policy": "仅 `status=escalate` 时**必填**（失败 V-* ID 列表）；`completed` 时不必填",
    },
    "gate": {
        "status_matrix": "| phase=`gate` | `pass` / `fail` |",
        "report_policy": "**必填**（报告文件绝对路径，文件必须已写盘）",
        "evidence_policy": "**至少 1 个**（证据文件绝对路径，可多次传）",
        "tasks_updated_policy": "选填",
    },
    "final-gate": {
        "status_matrix": "| phase=`final-gate` | `pass` / `fail` |",
        "report_policy": "**必填**（报告文件绝对路径，文件必须已写盘）",
        "evidence_policy": "**至少 1 个**（证据文件绝对路径，可多次传）",
        "tasks_updated_policy": "选填",
    },
    "fix": {
        "status_matrix": "| phase=`fix` | `completed` / `failed` |",
        "report_policy": "选填",
        "evidence_policy": "选填",
        "tasks_updated_policy": "**必填**（修复的 V-* 或 task_id 列表，可多次传或逗号分隔）",
    },
    "fix-gate": {
        "status_matrix": "| phase=`fix-gate` | `completed` / `failed` |",
        "report_policy": "选填",
        "evidence_policy": "选填",
        "tasks_updated_policy": "**必填**（修复的 V-* 或 task_id 列表，可多次传或逗号分隔）",
    },
    "simple": {
        "status_matrix": "| phase=`simple` | `completed` / `failed` |",
        "report_policy": "选填",
        "evidence_policy": "选填",
        "tasks_updated_policy": "选填",
    },
}


def _build_contract_phase_policies(phase: str) -> dict[str, str]:
    """返回当前 phase 对应的契约策略占位符（缺省为 empty policy，不抛错）。"""
    return _CONTRACT_PHASE_POLICIES.get(
        phase,
        {
            "status_matrix": "| phase=`" + phase + "` | （未知 phase，使用前请补 _CONTRACT_PHASE_POLICIES） |",
            "report_policy": "选填",
            "evidence_policy": "选填",
            "tasks_updated_policy": "选填",
        },
    )


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
    block_verify_mandatory = ""

    # 注：v2.2 起 blocks/hooks.yaml 内容已整合到 base.yaml 的 header_env 段，
    # 由 renderer 根据 phase 条件注入，不再单独加载。

    rollback_path = os.path.join(blocks_dir, "rollback.yaml")
    if os.path.isfile(rollback_path):
        rollback_data = _load_yaml(rollback_path)
        block_rollback = rollback_data.get("prompt", "")

    # v3.x: 集成验证硬性约束块（仅 phase=verify + env_required=true 时注入）
    verify_mandatory_path = os.path.join(blocks_dir, "verify_mandatory.yaml")
    if os.path.isfile(verify_mandatory_path):
        vmd = _load_yaml(verify_mandatory_path)
        # 不直接读取 prompt 字段；本块以 verify_mandatory 键注入，由渲染时条件判断
        block_verify_mandatory = vmd.get("verify_mandatory", "")

    tasks_path = os.path.join(blocks_dir, "tasks.yaml")
    if os.path.isfile(tasks_path):
        tasks_data = _load_yaml(tasks_path)
        block_tasks = tasks_data.get("prompt", "")

    # v2.1 新增：sub-agent 返回契约块（强制 JSON schema）
    contract_path = os.path.join(blocks_dir, "sub_agent_contract.yaml")
    if os.path.isfile(contract_path):
        contract_data = _load_yaml(contract_path)
        block_contract = contract_data.get("block", "")

    # v2.6 新增：按 phase 注入契约块的策略占位符（status 矩阵 / report / evidence / tasks_updated 必填策略）
    # 让同一份 sub_agent_contract.yaml 渲染出与当前 phase 匹配的精简字段表
    _phase_policies = _build_contract_phase_policies(phase)
    ctx_phase_policies = {
        "status_matrix_for_phase": _phase_policies["status_matrix"],
        "report_policy": _phase_policies["report_policy"],
        "evidence_policy": _phase_policies["evidence_policy"],
        "tasks_updated_policy": _phase_policies["tasks_updated_policy"],
    }

    # v2.2: 按 phase 决定是否注入 env 块
    #  - PHASES_WITH_ENV (dev/verify/fix/fix-gate): 注入 header_env（紧跟 Stage 配置，含
    #    env_instances + env_hooks + 运行时环境操作指令 + ROLE/INSTANCE 来源解释）
    #  - PHASES_WITHOUT_ENV (test/gate/simple/final-gate): 跳过 header_env
    inject_env = phase in PHASES_WITH_ENV or phase == "scenario-prepare"

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
    # v3.x: verify_mandatory 块（phase=verify + env_required=true 时注入）
    # 在 tasks 块之后、rollback 之前（让硬性约束优先于修复上下文）
    if block_verify_mandatory and phase == "verify" and ctx.get("env_required") is True:
        sections.append(_safe_format(block_verify_mandatory, ctx))
    if block_rollback and ctx.get("rollback_context"):
        sections.append(_safe_format(block_rollback, ctx))
    # v2.1: 契约块始终在最后，强调返回值约束
    if block_contract:
        # v2.6: 把 phase 策略注入 ctx，_safe_format 才能替换契约块里的占位符
        sections.append(_safe_format(block_contract, {**ctx, **ctx_phase_policies}))

    result = "\n\n---\n\n".join(sections)

    # 5. v3.x: 替换特殊占位符（rule_docs / p0_checks）.
    # 这些占位符在 YAML 模板里用 __XXX__ 标记（避免 `{` 被 YAML 解析为 flow mapping）。
    # 替换在所有 _safe_format 完成后执行，支持多行 markdown 内容。
    rule_docs_yaml = ctx.get("code_review_rule_docs_yaml") or ""
    p0_checks = ctx.get("code_review_p0_checks") or []
    p0_checks_str = ", ".join(p0_checks) if p0_checks else "（无）"
    result = result.replace("__RULE_DOCS_PLACEHOLDER__", rule_docs_yaml)
    result = result.replace("__P0_CHECKS_PLACEHOLDER__", p0_checks_str)

    # 6. build_rules prompt injection — prepend 在 prompt 最前,
    #    append 在 prompt 最后（晚于 sub_agent_contract 块）。
    #    与 pg-build v1 的 _merge_prompt_injection 行为一致。
    build_rules_prepend = (ctx.get("build_rules_prepend") or "").strip()
    build_rules_append = (ctx.get("build_rules_append") or "").strip()
    if build_rules_prepend:
        result = build_rules_prepend + "\n\n---\n\n" + result
    if build_rules_append:
        result = result + "\n\n---\n\n" + build_rules_append

    return result


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