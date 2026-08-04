"""Tests for pg init development-tool adapters."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from integrations import (  # noqa: E402
    IntegrationContext,
    detect_tools,
    get_integration,
    load_selected_tool,
    save_selected_tool,
    supported_tools,
)
from core.rendering import (  # noqa: E402
    TOKEN_PATTERN,
    WorkflowRenderError,
    render_workflow_text,
)
from core.init import (  # noqa: E402
    InitOptions,
    ToolSelectionError,
    refresh_configured_integration,
    select_tool,
)


class TestIntegrationRegistry(unittest.TestCase):
    def test_supported_tools(self):
        self.assertEqual(supported_tools(), ("mobile-coder", "opencode"))

    def test_selected_tool_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / ".pg").mkdir()
            self.assertEqual(load_selected_tool(project), "opencode")
            save_selected_tool(project, "mobile-coder")
            self.assertEqual(load_selected_tool(project), "mobile-coder")

    def test_aliases_are_normalized(self):
        self.assertEqual(get_integration("mobile_coder").tool_id, "mobile-coder")

    def test_detection_uses_project_markers(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / ".mobile-coder").mkdir()
            detected = detect_tools(project, REPO_ROOT)
            self.assertEqual(detected[0].tool_id, "mobile-coder")
            self.assertGreaterEqual(detected[0].confidence, 80)


class TestArchitecture(unittest.TestCase):
    def test_core_and_tool_packages_are_separated(self):
        self.assertTrue((REPO_ROOT / "src" / "core" / "init.py").is_file())
        self.assertTrue((REPO_ROOT / "src" / "core" / "doctor.py").is_file())
        self.assertTrue((REPO_ROOT / "src" / "core" / "workflows").is_dir())
        self.assertFalse((REPO_ROOT / "src" / "opencode").exists())

        for tool in ("opencode", "mobile_coder"):
            package = REPO_ROOT / "src" / "integrations" / tool
            self.assertTrue((package / "adapter.py").is_file())
            self.assertTrue((package / "templates" / "integration.json").is_file())

    def test_mobile_coder_adapter_does_not_import_opencode_adapter(self):
        source = (
            REPO_ROOT
            / "src"
            / "integrations"
            / "mobile_coder"
            / "adapter.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("integrations.opencode", source)
        self.assertNotIn("from ..opencode", source)

    def test_core_workflows_use_only_tool_neutral_vocabulary(self):
        core_root = REPO_ROOT / "src" / "core" / "workflows"
        text_suffixes = {".md", ".py", ".sh", ".json", ".yaml", ".yml", ".txt"}
        forbidden = (
            ".opencode/",
            "src/opencode",
            "Skill tool",
            "Task tool",
            "question tool",
            "TodoWrite",
            "subagent_type",
            "opencode run",
            "pg-router/",
        )
        for path in core_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in text_suffixes:
                continue
            source = path.read_text(encoding="utf-8")
            for value in forbidden:
                self.assertNotIn(value, source, path)

    def test_every_workflow_token_is_supported_by_every_adapter(self):
        workflow_root = REPO_ROOT / "src" / "core" / "workflows"
        tokens = set()
        for path in workflow_root.rglob("*"):
            if path.is_file() and path.suffix.lower() in {
                ".md", ".py", ".sh", ".json", ".yaml", ".yml", ".html", ".txt"
            }:
                tokens.update(
                    match.group(1)
                    for match in TOKEN_PATTERN.finditer(
                        path.read_text(encoding="utf-8")
                    )
                )

        self.assertTrue(tokens)
        for tool_id in supported_tools():
            variables = get_integration(tool_id).template_variables()
            self.assertEqual(tokens - variables.keys(), set(), tool_id)

    def test_renderer_rejects_an_unsupported_action(self):
        with self.assertRaises(WorkflowRenderError):
            render_workflow_text(
                "Use {{pg:action.unknown}}",
                get_integration("opencode").template_variables(),
                source="test.md",
            )


class TestToolSelection(unittest.TestCase):
    def test_explicit_tool_bypasses_detection(self):
        selected = select_tool(
            Path.cwd(),
            REPO_ROOT,
            InitOptions(tool="opencode", non_interactive=True),
            input_is_tty=False,
        )
        self.assertEqual(selected, "opencode")

    def test_invalid_explicit_tool_is_reported_as_selection_error(self):
        with self.assertRaisesRegex(ToolSelectionError, "Unsupported tool"):
            select_tool(
                Path.cwd(),
                REPO_ROOT,
                InitOptions(tool="unknown-tool", non_interactive=True),
                input_is_tty=False,
            )

    def test_non_interactive_requires_explicit_tool(self):
        with self.assertRaises(ToolSelectionError):
            select_tool(
                Path.cwd(),
                REPO_ROOT,
                InitOptions(non_interactive=True),
                input_is_tty=False,
            )

    def test_single_detected_tool_is_confirmed(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / ".mobile-coder").mkdir()
            selected = select_tool(
                project,
                REPO_ROOT,
                InitOptions(),
                input_fn=lambda _: "",
                output=lambda _: None,
                input_is_tty=True,
            )
            self.assertEqual(selected, "mobile-coder")

    def test_multiple_detected_tools_are_selected_interactively(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / ".mobile-coder").mkdir()
            (project / ".opencode").mkdir()
            selected = select_tool(
                project,
                REPO_ROOT,
                InitOptions(),
                input_fn=lambda _: "opencode",
                output=lambda _: None,
                input_is_tty=True,
            )
            self.assertEqual(selected, "opencode")

    def test_saved_selection_does_not_hide_multiple_project_markers(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / ".pg").mkdir()
            (project / ".mobile-coder").mkdir()
            (project / ".opencode").mkdir()
            save_selected_tool(project, "mobile-coder")

            selected = select_tool(
                project,
                REPO_ROOT,
                InitOptions(),
                input_fn=lambda _: "opencode",
                output=lambda _: None,
                input_is_tty=True,
            )
            self.assertEqual(selected, "opencode")

    def test_refresh_requires_a_saved_tool_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / ".pg").mkdir()
            with self.assertRaisesRegex(
                ToolSelectionError,
                "No configured development-tool integration",
            ):
                refresh_configured_integration(project, REPO_ROOT)


class TestMobileCoderIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / ".pg").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_install_generates_native_mobile_coder_adapter(self):
        source_command = (
            REPO_ROOT
            / "src"
            / "core"
            / "workflows"
            / "commands"
            / "pg-3-build.md"
        )
        source_before = source_command.read_bytes()

        result = get_integration("mobile-coder").install(
            IntegrationContext(self.project, REPO_ROOT)
        )

        mobile = self.project / ".mobile-coder"
        self.assertTrue((mobile / "commands" / "pg-3-build.md").is_file())
        self.assertTrue((mobile / "agents" / "pg-build" / "test.md").is_file())
        self.assertTrue((mobile / "skills" / "pg-build" / "SKILL.md").is_file())
        self.assertTrue(
            (mobile / "pg-skills" / "src" / "runtime" / "bin" / "pg-invoke-hook.py").is_file()
        )
        self.assertFalse(
            (mobile / "pg-skills" / "src" / "runtime" / "bin" / "pg").exists()
        )
        self.assertFalse((self.project / ".agents").exists())

        self.assertFalse((mobile / "mobile-coder.json").exists())

        build_template = (mobile / "commands" / "pg-3-build.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("name: 3-pg-build", build_template)
        self.assertIn("agent: pg-manager", build_template)
        self.assertIn("Treat runner action `done` as a transition", build_template)
        self.assertIn("pg-verify-and-merge", build_template)

        manager_prompt = (mobile / "agents" / "pg-manager.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("name: pg-manager", manager_prompt)
        self.assertIn("mode: primary", manager_prompt)
        self.assertIn("subagent: allow", manager_prompt)
        self.assertIn("configured default branch", manager_prompt)
        self.assertIn("business changes", manager_prompt)

        test_agent = (mobile / "agents" / "pg-build" / "test.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("mode: subagent", test_agent)
        self.assertIn("subagent: allow", test_agent)
        scenario_agent = (
            mobile / "agents" / "pg-build" / "scenario-execute.md"
        ).read_text(encoding="utf-8")
        self.assertIn("webfetch: allow", scenario_agent)
        self.assertTrue(
            (mobile / "skills" / "pg-verify-and-merge" / "SKILL.md").is_file()
        )
        build_command = (mobile / "commands" / "pg-3-build.md").read_text(encoding="utf-8")
        self.assertIn(".mobile-coder/skills/pg-build", build_command)
        self.assertNotIn(".opencode/", build_command)
        browser_skill = (
            mobile
            / "skills"
            / "pg-browser-testing-with-devtools"
            / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("${CHROME_PATH}", browser_skill)
        for root_name in ("commands", "agents", "skills"):
            for generated_file in (mobile / root_name).rglob("*"):
                if generated_file.is_file() and generated_file.suffix.lower() in {
                    ".md", ".py", ".sh", ".json", ".yaml", ".yml", ".txt"
                }:
                    generated_text = generated_file.read_text(encoding="utf-8")
                    self.assertNotIn(".opencode/", generated_text, generated_file)
                    self.assertNotIn("{{pg:", generated_text, generated_file)
        self.assertEqual(source_command.read_bytes(), source_before)
        self.assertTrue(any("installed" in message for message in result.messages))

    def test_install_never_creates_or_modifies_mobile_coder_config(self):
        integration = get_integration("mobile-coder")
        context = IntegrationContext(self.project, REPO_ROOT)
        mobile = self.project / ".mobile-coder"
        mobile.mkdir()
        config = mobile / "mobile-coder.json"
        original = b'{\n  // user-owned JSONC\n  "theme": "custom"\n}\n'
        config.write_bytes(original)
        legacy_manifest = {
            "schema_version": 1,
            "tool": "mobile-coder",
            "files": {"mobile-coder.json": hashlib.sha256(original).hexdigest()},
        }
        (mobile / ".pg-adapter-manifest.json").write_text(
            json.dumps(legacy_manifest),
            encoding="utf-8",
        )

        first_result = integration.install(context)
        integration.install(context)

        self.assertEqual(config.read_bytes(), original)
        self.assertTrue(
            any("preserved legacy mobile-coder.json" in item for item in first_result.warnings)
        )
        manifest = json.loads(
            (mobile / ".pg-adapter-manifest.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("mobile-coder.json", manifest["files"])

    def test_reinstall_preserves_modified_generated_file_and_custom_file(self):
        integration = get_integration("mobile-coder")
        context = IntegrationContext(self.project, REPO_ROOT)
        integration.install(context)

        command = self.project / ".mobile-coder" / "commands" / "pg-3-build.md"
        command.write_text("custom command\n", encoding="utf-8")
        custom = self.project / ".mobile-coder" / "commands" / "my-command.md"
        custom.write_text("custom project command\n", encoding="utf-8")

        result = integration.install(context)
        integration.install(context)

        self.assertEqual(command.read_text(encoding="utf-8"), "custom command\n")
        self.assertEqual(custom.read_text(encoding="utf-8"), "custom project command\n")
        self.assertTrue(
            any("preserved modified file" in warning for warning in result.warnings)
        )

    def test_install_replaces_dangling_symlink_without_crash(self):
        """Dangling symlinks (e.g. from a moved-out pg-skills checkout)
        must be cleaned up by the legacy sweep, not crash the write loop
        with FileNotFoundError (Python follows symlinks on open())."""
        integration = get_integration("mobile-coder")
        context = IntegrationContext(self.project, REPO_ROOT)

        # Pre-create the mobile root, then plant a dangling symlink at a
        # rendered surface path. The target does not exist on disk.
        mobile = self.project / ".mobile-coder"
        mobile.mkdir()
        target = mobile / "commands" / "pg-3-build.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to("/nonexistent/path/that/does/not/exist")

        # First install replaces the dangling symlink and writes the file.
        try:
            integration.install(context)
        except (OSError, FileNotFoundError) as exc:  # pragma: no cover
            self.fail(f"install() crashed on dangling symlink: {exc}")

        self.assertFalse(target.is_symlink())
        self.assertTrue(target.is_file())
        # Rendered content must not be the user's (non-existent) data.
        self.assertNotEqual(target.read_text(encoding="utf-8"), "")

        # Re-plant a dangling symlink at a *different* rendered surface
        # path, then re-install, to capture the migration message in
        # result.messages.
        second = mobile / "commands" / "pg-1-define.md"
        if second.exists() or second.is_symlink():
            second.unlink()
        second.symlink_to("/nonexistent/another-dangling-target")
        result = integration.install(context)
        self.assertFalse(second.is_symlink())
        self.assertTrue(
            any("migrated legacy" in message for message in result.messages),
            result.messages,
        )

    def test_install_replaces_out_of_tree_legacy_symlink(self):
        """Symlinks pointing to a real file outside the current workflow
        root (an old pg-skills checkout at a sibling absolute path) must
        be replaced by the legacy sweep instead of being preserved."""
        integration = get_integration("mobile-coder")
        context = IntegrationContext(self.project, REPO_ROOT)

        # Build an "old" pg-skills tree outside the workflow root whose
        # file content would otherwise satisfy the "below source_root" check.
        old_root = self.project / "_old_pg_skills" / "src" / "core" / "workflows"
        (old_root / "commands").mkdir(parents=True, exist_ok=True)
        old_command = old_root / "commands" / "pg-3-build.md"
        old_command.write_text(
            "stale content from a removed pg-skills checkout\n",
            encoding="utf-8",
        )

        mobile = self.project / ".mobile-coder"
        mobile.mkdir()
        target = mobile / "commands" / "pg-3-build.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(old_command)

        try:
            integration.install(context)
        except (OSError, FileNotFoundError) as exc:  # pragma: no cover
            self.fail(f"install() crashed on out-of-tree symlink: {exc}")

        self.assertFalse(target.is_symlink())
        self.assertNotEqual(
            target.read_text(encoding="utf-8"),
            "stale content from a removed pg-skills checkout\n",
        )

    def test_install_silently_replaces_user_shadow_symlink(self):
        """User-created symlinks at a rendered surface are silently
        replaced by the rendered file (rendered surfaces are owned by
        pg-skills). No warning is emitted for the replacement itself."""
        integration = get_integration("mobile-coder")
        context = IntegrationContext(self.project, REPO_ROOT)

        # A real file the user symlinked into the rendered surface.
        user_file = self.project / "user-content.md"
        user_file.write_text("# user content\n", encoding="utf-8")

        mobile = self.project / ".mobile-coder"
        mobile.mkdir()
        target = mobile / "commands" / "pg-3-build.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(user_file)

        result = integration.install(context)

        self.assertFalse(target.is_symlink())
        self.assertTrue(target.is_file())
        # Rendered content wins, user's content is gone (silent overwrite).
        self.assertNotEqual(target.read_text(encoding="utf-8"), "# user content\n")
        # No "preserved" warning for this file (silent overwrite contract).
        self.assertFalse(
            any(
                "pg-3-build.md" in warning
                for warning in result.warnings
                if "preserved" in warning
            ),
            result.warnings,
        )


class TestOpenCodeIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _install(self):
        try:
            return get_integration("opencode").install(
                IntegrationContext(self.project, REPO_ROOT)
            )
        except OSError as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")

    def test_install_renders_managed_opencode_workflow_tree(self):
        self._install()

        workflow_root = REPO_ROOT / "src" / "core" / "workflows"
        for surface in ("commands", "agents", "skills"):
            source_root = workflow_root / surface
            target_root = self.project / ".opencode" / surface
            self.assertTrue(target_root.is_dir())
            self.assertEqual(
                {entry.name for entry in target_root.iterdir()},
                {entry.name for entry in source_root.iterdir()},
            )
            for source in source_root.iterdir():
                target = target_root / source.name
                self.assertTrue(target.exists(), target)
                self.assertFalse(target.is_symlink(), target)
        self.assertTrue(
            (self.project / ".opencode" / ".pg-adapter-manifest.json").is_file()
        )

    def test_install_preserves_opencode_workflow_semantics(self):
        self._install()

        build_command = (
            self.project / ".opencode" / "commands" / "pg-3-build.md"
        ).read_text(encoding="utf-8")
        manager = (
            self.project / ".opencode" / "agents" / "pg-manager.md"
        ).read_text(encoding="utf-8")

        self.assertIn("使用 Skill tool 加载 `pg-build` skill", build_command)
        self.assertIn("question tool", build_command)
        self.assertIn("task: allow", manager)
        self.assertIn("Skill tool", manager)
        self.assertIn("model: pg-router/pg-associate", manager)
        browser_skill = (
            self.project
            / ".opencode"
            / "skills"
            / "pg-browser-testing-with-devtools"
            / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("{env:CHROME_PATH}", browser_skill)
        self.assertNotIn("Mobile Coder", build_command)
        self.assertNotIn("Mobile Coder", manager)
        self.assertNotIn("{{pg:", build_command)
        self.assertNotIn("{{pg:", manager)
        for root_name in ("commands", "agents", "skills"):
            for generated_file in (
                self.project / ".opencode" / root_name
            ).rglob("*"):
                if generated_file.is_file() and generated_file.suffix.lower() in {
                    ".md",
                    ".py",
                    ".sh",
                    ".json",
                    ".yaml",
                    ".yml",
                    ".html",
                    ".txt",
                }:
                    generated_text = generated_file.read_text(encoding="utf-8")
                    self.assertNotIn("{{pg:", generated_text, generated_file)

    def test_reinstall_is_idempotent_and_preserves_custom_paths(self):
        self._install()
        build_command = (
            self.project / ".opencode" / "commands" / "pg-3-build.md"
        )
        build_command.unlink()
        build_command.write_text("custom build command\n", encoding="utf-8")
        custom = self.project / ".opencode" / "commands" / "custom.md"
        custom.write_text("project command\n", encoding="utf-8")

        result = self._install()

        self.assertEqual(
            build_command.read_text(encoding="utf-8"),
            "custom build command\n",
        )
        self.assertEqual(custom.read_text(encoding="utf-8"), "project command\n")
        self.assertTrue(
            any("pg-3-build.md" in warning for warning in result.warnings)
        )

    def test_legacy_pg_skills_link_is_migrated(self):
        self._install()
        stale = self.project / ".opencode" / "commands" / "stale-pg-command.md"
        stale_source = (
            REPO_ROOT
            / "src"
            / "core"
            / "workflows"
            / "commands"
            / "stale-pg-command.md"
        )
        stale.symlink_to(stale_source)

        result = self._install()

        self.assertFalse(stale.is_symlink())
        self.assertTrue(
            any("migrated legacy" in message for message in result.messages)
        )

    def test_install_replaces_dangling_symlink_without_crash(self):
        """Dangling symlinks at a rendered surface path (e.g. leftover
        from a pg-skills checkout that has been moved in-tree) must be
        unlinked before the write loop, otherwise ``Path.write_bytes``
        crashes with FileNotFoundError because Python follows symlinks
        on open()."""
        self._install()
        target = self.project / ".opencode" / "commands" / "pg-1-define.md"
        target.unlink()
        target.symlink_to("/nonexistent/path/that/does/not/exist")

        try:
            result = self._install()
        except (OSError, FileNotFoundError) as exc:  # pragma: no cover
            self.fail(f"install() crashed on dangling symlink: {exc}")

        self.assertFalse(target.is_symlink())
        self.assertTrue(target.is_file())
        self.assertTrue(
            any("migrated legacy" in message for message in result.messages),
            result.messages,
        )

    def test_install_replaces_out_of_tree_legacy_symlink(self):
        """A symlink that resolves to a real file outside the current
        workflow root (e.g. an old sibling /home/.../pg-skills checkout)
        must be replaced by the legacy sweep. Previously the
        ``_is_below(source_root)`` check missed these and crashed on
        write."""
        self._install()
        target = self.project / ".opencode" / "commands" / "pg-1-define.md"

        old_root = self.project / "_old_pg_skills" / "src" / "core" / "workflows"
        (old_root / "commands").mkdir(parents=True, exist_ok=True)
        old_command = old_root / "commands" / "pg-1-define.md"
        old_command.write_text(
            "stale content from a removed pg-skills checkout\n",
            encoding="utf-8",
        )

        target.unlink()
        target.symlink_to(old_command)

        try:
            self._install()
        except (OSError, FileNotFoundError) as exc:  # pragma: no cover
            self.fail(f"install() crashed on out-of-tree symlink: {exc}")

        self.assertFalse(target.is_symlink())
        self.assertNotEqual(
            target.read_text(encoding="utf-8"),
            "stale content from a removed pg-skills checkout\n",
        )

    def test_install_silently_replaces_user_shadow_symlink(self):
        """User-created symlinks at a rendered surface are silently
        replaced by the rendered file (rendered surfaces are owned by
        pg-skills). No ``preserved`` warning is emitted for the
        replacement itself."""
        self._install()
        target = self.project / ".opencode" / "commands" / "pg-1-define.md"

        user_file = self.project / "user-content.md"
        user_file.write_text("# user content\n", encoding="utf-8")

        target.unlink()
        target.symlink_to(user_file)

        result = self._install()

        self.assertFalse(target.is_symlink())
        self.assertTrue(target.is_file())
        # Rendered content wins.
        self.assertNotEqual(target.read_text(encoding="utf-8"), "# user content\n")
        # Silent overwrite contract: no "preserved" warning for this file.
        self.assertFalse(
            any(
                "pg-1-define.md" in warning
                for warning in result.warnings
                if "preserved" in warning
            ),
            result.warnings,
        )


class TestCli(unittest.TestCase):
    def test_init_lists_tools_without_project(self):
        completed = subprocess.run(
            [sys.executable, str(REPO_ROOT / "src" / "runtime" / "bin" / "pg"),
             "init", "--list-tools"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("mobile-coder", completed.stdout)
        self.assertIn("opencode", completed.stdout)

    def test_non_interactive_init_requires_explicit_tool(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / ".pg").mkdir()
            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "src" / "runtime" / "bin" / "pg"),
                    "init",
                    "--non-interactive",
                ],
                cwd=str(project),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("No --tool was provided", completed.stdout)

    def test_explicit_mobile_coder_init_installs_selected_adapter(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / ".pg").mkdir()
            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "src" / "runtime" / "bin" / "pg"),
                    "init",
                    "--non-interactive",
                    "--tool",
                    "mobile-coder",
                ],
                cwd=str(project),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            mobile = project / ".mobile-coder"
            self.assertFalse((mobile / "mobile-coder.json").exists())
            self.assertTrue((mobile / "commands" / "pg-3-build.md").is_file())
            self.assertTrue((mobile / "agents" / "pg-manager.md").is_file())
            self.assertTrue((mobile / "skills" / "pg-build" / "SKILL.md").is_file())
            self.assertFalse((project / ".agents").exists())
            state = json.loads(
                (project / ".pg" / "tool-integration.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["tool"], "mobile-coder")

    def test_explicit_opencode_init_preserves_original_project_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / ".pg").mkdir()
            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "src" / "runtime" / "bin" / "pg"),
                    "init",
                    "--non-interactive",
                    "--tool",
                    "opencode",
                ],
                cwd=str(project),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((project / ".pg" / "project.yaml").is_file())
            self.assertTrue((project / "pg-run").exists())
            if sys.platform == "win32":
                self.assertTrue((project / "pg-run.cmd").is_file())
            self.assertTrue(
                (project / ".opencode" / "commands" / "pg-3-build.md").is_file()
            )
            self.assertTrue(
                (project / ".opencode" / "agents" / "pg-manager.md").is_file()
            )
            self.assertTrue(
                (project / ".opencode" / "skills" / "pg-build").is_dir()
            )
            state = json.loads(
                (project / ".pg" / "tool-integration.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["tool"], "opencode")

            doctor = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "src" / "runtime" / "bin" / "pg"),
                    "doctor",
                ],
                cwd=str(project),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
            self.assertIn(".pg/project.yaml schema valid", doctor.stdout)


if __name__ == "__main__":
    unittest.main()
