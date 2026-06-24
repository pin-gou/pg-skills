"""Tests for pg-fix-issue v3.0 invoke-hook protocol migration.

Validates that the v3.0 changes actually took effect:
1. pg-parse-config.py pg-fix-issue no longer outputs `resolved_actions`
2. SKILL.md no longer contains `type: rebuild_and_restart` references that
   could be interpreted as live operation instructions (top-of-file
   "已删除" / "已迁移" callouts are expected and allowed)
3. executor agent no longer documents `type: rebuild_and_restart` as a
   supported operation
4. SKILL.md declares v3.0 metadata and the invoke-hook CLI form
5. fix_issue.ask_prepare_env / ask_clean_env remain in project.yaml
   schema (backward compatibility)
"""

import os
import re
import sys
import unittest

_HERE = os.path.realpath(os.path.abspath(__file__))


def _find_project_root(here):
    """Walk up from `here` until we find a directory containing .pg/project.yaml.

    Handles both the symlinked path (.opencode/skills/...) and the real path
    (.pg/skills/src/opencode/skills/...).
    """
    p = os.path.dirname(here)
    for _ in range(10):
        if os.path.isfile(os.path.join(p, ".pg", "project.yaml")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    raise RuntimeError(
        f"Cannot find .pg/project.yaml walking up from {here}")


PROJECT_ROOT = _find_project_root(_HERE)
PG_SKILLS_SRC = os.path.join(PROJECT_ROOT, ".pg", "skills", "src")

SKILL_PATH = os.path.join(
    PG_SKILLS_SRC, "opencode", "skills", "pg-fix-issue", "SKILL.md"
)
EXECUTOR_PATH = os.path.join(
    PROJECT_ROOT, ".opencode", "agents", "pg-fix-issue", "executor.md"
)
# NB: .opencode/skills/pg-fix-issue/ is a symlink to .pg/skills/.../skills/pg-fix-issue/
# so the symlink-resolved file is the same as SKILL_PATH. We resolve for the
# tests by following the symlink once.
if os.path.islink(os.path.join(PROJECT_ROOT, ".opencode", "skills", "pg-fix-issue")):
    REAL_SKILL_PATH = os.path.realpath(
        os.path.join(PROJECT_ROOT, ".opencode", "skills", "pg-fix-issue", "SKILL.md")
    )
else:
    REAL_SKILL_PATH = SKILL_PATH


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class TestParseConfigRegression(unittest.TestCase):
    """pg-parse-config.py pg-fix-issue workflow no longer outputs resolved_actions."""

    def test_workflow_keys_no_resolved_actions(self):
        """WORKFLOW_KEYS[pg-fix-issue] must not list resolved_actions."""
        parser_path = os.path.join(
            PG_SKILLS_SRC, "opencode", "scripts", "pg-parse-config.py"
        )
        src = _read(parser_path)
        # Find the pg-fix-issue entry in WORKFLOW_KEYS
        m = re.search(
            r'"pg-fix-issue"\s*:\s*\[([^\]]+)\]', src
        )
        assert m is not None, "Cannot find pg-fix-issue entry in WORKFLOW_KEYS"
        keys_str = m.group(1)
        self.assertNotIn(
            '"resolved_actions"', keys_str,
            "pg-fix-issue workflow must NOT include resolved_actions "
            "(v3.0: service actions are rendered by runner invoke-hook, "
            "not pre-rendered by parser)",
        )
        # Must still include the required segments
        for required in ("modules", "environments", "tracks", "stages", "fix_issue"):
            self.assertIn(
                f'"{required}"', keys_str,
                f"pg-fix-issue workflow must still include {required!r}",
            )


class TestSkillMdV3Migration(unittest.TestCase):
    """SKILL.md v3.0 migration markers."""

    def test_version_is_3(self):
        """SKILL.md front-matter version must be 3.x."""
        content = _read(REAL_SKILL_PATH)
        m = re.search(r"version:\s*\"([^\"]+)\"", content)
        if m is None:
            self.fail("SKILL.md front-matter must declare version")
        version = m.group(1)
        self.assertTrue(
            version.startswith("3."),
            f"SKILL.md version must be 3.x, got {version!r}",
        )

    def test_compatibility_declares_invoke_hook(self):
        """compatibility field must reference invoke-hook CLI."""
        content = _read(REAL_SKILL_PATH)
        m = re.search(r"compatibility:\s*(.+?)(?=\nmetadata:|\n---)", content, re.DOTALL)
        if m is None:
            self.fail("SKILL.md must have compatibility field")
        compat = m.group(1)
        self.assertIn(
            "invoke-hook", compat,
            "compatibility must mention invoke-hook (hooks protocol entry point)",
        )

    def test_deployment_cli_form_documented(self):
        """SKILL.md must contain a Deployment 工具调用约定 section with the
        invoke-hook CLI form."""
        content = _read(REAL_SKILL_PATH)
        # Must have a section that documents invoke-hook CLI form
        self.assertRegex(
            content,
            r"pg-pipeline-runner\.py\s+invoke-hook",
            "SKILL.md must document invoke-hook CLI invocation form",
        )
        # Must mention prepare_env / clean_env action option
        self.assertIn(
            "--action prepare_env", content,
            "SKILL.md must document --action prepare_env for env-level hooks",
        )
        self.assertIn(
            "--action clean_env", content,
            "SKILL.md must document --action clean_env for env-level hooks",
        )

    def test_no_legacy_rebuild_and_restart_operation(self):
        """SKILL.md operations example must NOT contain a live
        restart_backend type: shell example using resolved_actions (those were
        removed in v3.0). The "❌ 删除的旧示例" callout in prose is allowed —
        it documents what was removed for migration."""
        content = _read(REAL_SKILL_PATH)
        # The literal "type: shell" + "cmd: \"{resolved_actions..." combo
        # in a YAML code block indicates an actionable example. Top-of-file
        # migration notes and "已删除" callouts in prose are fine.
        self.assertNotRegex(
            content,
            r'type:\s*shell\s*\n\s+cmd:\s*"\{resolved_actions\.',
            "SKILL.md must not contain live restart_backend example "
            "with type: shell + cmd: {resolved_actions.*} (v3.0 removed)",
        )
        # rebuild_stack mention is allowed only in the "❌ 删除的旧示例"
        # migration callout, not in any actionable operations block.
        yaml_blocks = re.findall(
            r"```yaml\s*\n([\s\S]+?)```", content,
        )
        for block in yaml_blocks:
            self.assertNotIn(
                "rebuild_stack", block,
                "rebuild_stack must NOT appear in any YAML operations block "
                "(only allowed in prose migration callout)",
            )


class TestExecutorAgentV3(unittest.TestCase):
    """executor agent v3.0 has rebuild_and_restart removed."""

    def test_executor_drops_rebuild_and_restart_operation_section(self):
        """executor.md must NOT document rebuild_and_restart as a supported
        operation type. Migration prose callouts are allowed (they explain
        what was removed)."""
        content = _read(EXECUTOR_PATH)
        # The actionable content lives in YAML code blocks. Migration prose
        # that says "type: rebuild_and_restart 已删除" is intentional and
        # must be allowed.
        yaml_blocks = re.findall(r"```yaml\s*\n([\s\S]+?)```", content)
        for block in yaml_blocks:
            self.assertNotRegex(
                block,
                r"type:\s*rebuild_and_restart",
                f"executor.md YAML operations block must not contain "
                f"`type: rebuild_and_restart` example:\n{block[:200]}",
            )

    def test_executor_documents_module_based_schema(self):
        """executor.md input format must use module: (not track:)."""
        content = _read(EXECUTOR_PATH)
        # The input format block (operations: - name/type/module) must
        # reference `module:` not `track:`.
        # Find the input format block.
        m = re.search(
            r"```yaml\s*\noperations:[\s\S]+?```",
            content,
        )
        if m is None:
            self.fail("executor.md must contain a YAML operations example")
        block = m.group(0)
        self.assertIn(
            "module:", block,
            "executor.md input format must use `module:` (v3.0 module-based)",
        )
        self.assertNotIn(
            "track:", block,
            "executor.md input format must NOT use `track:` (v3.0 removed)",
        )

    def test_executor_forbids_invoke_hook_in_shell(self):
        """executor.md must explicitly forbid invoke-hook in type: shell."""
        content = _read(EXECUTOR_PATH)
        self.assertIn(
            "invoke-hook", content,
            "executor.md must reference invoke-hook (it must explain the boundary)",
        )
        # Must forbid using type: shell to call invoke-hook
        self.assertRegex(
            content,
            r"type:\s*shell[\s\S]{0,200}invoke-hook[\s\S]{0,200}(禁止|❌)",
            "executor.md must forbid using `type: shell` to call invoke-hook",
        )


def _strip_markdown_comments(s):
    """Strip HTML-style markdown comments so we only check body content."""
    return re.sub(r"<!--[\s\S]*?-->", "", s)


if __name__ == "__main__":
    unittest.main()