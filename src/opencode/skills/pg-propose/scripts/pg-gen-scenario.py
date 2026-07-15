#!/usr/bin/env python3
"""pg-gen-scenario.py — Generate scenario.yaml skeleton from on-conditions-eval.md decision.

Usage:
    python3 pg-gen-scenario.py <change>

Reads `.pg/changes/<change>/1-propose-review/on-conditions-eval.md` to find
`scenario_test_decision` segment (SSOT written by `pg-gen-tasks-skeleton.py`).

Behavior:
  - decision.enabled = true  → write `.pg/changes/<change>/scenario.yaml` skeleton
                                (LLM 必须在阶段三自审时填充 Scenario 内容)
  - decision.enabled = false → no-op (do NOT write scenario.yaml)
  - decision missing         → emit error: must run `pg-gen-tasks-skeleton.py` first

The generated skeleton contains 1 placeholder Scenario (S-example) that
LLM replaces with real scenarios. The schema matches the contract in
`pg-propose/SKILL.md` §产物清单 → scenario.yaml 生成指引.

This script is pure-function (zero side effects beyond writing scenario.yaml).
"""

import os
import sys

try:
    import yaml
except ImportError:
    print('{"error": "PyYAML is required. Install with: pip install pyyaml"}',
          file=sys.stderr)
    sys.exit(1)

# Add pg-propose scripts dir to path for imports
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

from pg_pipeline_common import CHANGES_DIR


# scenario_test_decision 段 marker (与 pg-gen-tasks-skeleton.py 同步)
_DECISION_MARKER = "## scenario_test_decision (v3.5)"


def _read_scenario_decision(change: str) -> dict | None:
    """从 on-conditions-eval.md 读取 scenario_test_decision 段。

    Returns:
        dict with keys {enabled: bool, reason: str, mode: str, source: str}
        或 None (eval.md 不存在 / 段缺失 / 解析失败)
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
    # 只取下一个 ## 之前的内容
    section = section.split("\n## ", 1)[0]

    def _val(line: str) -> str:
        return line.split("|", 2)[2].strip().strip("*").strip()

    decision: dict = {"enabled": False, "reason": "", "mode": "", "source": ""}
    for line in section.splitlines():
        if line.startswith("| enabled"):
            decision["enabled"] = (_val(line).lower() == "true")
        elif line.startswith("| mode"):
            decision["mode"] = _val(line)
        elif line.startswith("| source"):
            decision["source"] = _val(line)
        elif line.startswith("| reason"):
            decision["reason"] = _val(line)
    return decision if decision["mode"] else None


def _build_skeleton_yaml(change: str) -> dict:
    """构造 scenario.yaml skeleton —— LLM 在阶段三自审时填充。

    默认含 1 个 placeholder Scenario 让 LLM 看到 schema 形式。LLM 必须替换。
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
                    "2-build/<scenario_id>-evidence.json",
                ],
            },
        ],
        "_meta": {
            "_comment": (
                "scenario.yaml 由 pg-gen-scenario.py 生成的 skeleton, "
                "LLM 必填。scenario_id / given / when / then / and / evidence "
                "是必填段, critical / description 必填, _meta 段最终会被 pg-build "
                "scenario-execute agent 忽略。"
            ),
            "change": change,
            "schema_version": "v3.5",
        },
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 pg-gen-scenario.py <change>", file=sys.stderr)
        sys.exit(1)

    change = sys.argv[1]
    decision = _read_scenario_decision(change)

    if decision is None:
        print(
            f"ERROR: scenario_test_decision 段缺失, 必须先跑:\n"
            f"  python3 .opencode/skills/pg-propose/scripts/pg-gen-tasks-skeleton.py "
            f"--change {change} --scenario-test-enabled {{true|false}} --scenario-test-reason '...' ...",
            file=sys.stderr,
        )
        sys.exit(1)

    if not decision["enabled"]:
        # 禁用时: 不写 scenario.yaml (保持三个产物一致)
        result = {
            "scenario_yaml_written": None,
            "scenario_test_enabled": False,
            "reason": decision["reason"],
            "action": "skipped (scenario-test disabled)",
        }
        import json
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # 启用时: 写 scenario.yaml skeleton
    scenario_path = os.path.join(CHANGES_DIR, change, "scenario.yaml")
    os.makedirs(os.path.dirname(scenario_path), exist_ok=True)
    skeleton = _build_skeleton_yaml(change)
    with open(scenario_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            skeleton, f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )

    import json
    result = {
        "scenario_yaml_written": scenario_path,
        "scenario_test_enabled": True,
        "reason": decision["reason"],
        "action": "skeleton written (LLM must fill scenario content)",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
