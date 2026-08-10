"""Project initialization and development-tool selection."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from integrations import (
    IntegrationContext,
    ToolCandidate,
    detect_tools,
    get_integration,
    load_selected_tool,
    normalize_tool_id,
    save_selected_tool,
    supported_tools,
)


GITIGNORE_CONTENT = """\
# Runtime session directories (all auto-generated, never committed)
runs/
ad-hoc/
fix-issue/
agent/
regression/
skills.backup.*

# Dynamic generated files
*.profile
cronjobs/prompt.txt

# pg-init-project dynamic output (review-only, regenerated each run)
agents-md-patches.md

# Build artifacts within change sessions
changes/**/2-build/*.png
changes/**/2-build/*.txt
changes/**/*.log
"""

CHANGES_GITIGNORE_CONTENT = """\
# Build artifacts within change sessions
**/2-build/*.png
**/2-build/*.txt
**/*.log
"""


@dataclass(frozen=True)
class InitOptions:
    tool: str | None = None
    list_tools: bool = False
    skip_tool_config: bool = False
    assume_yes: bool = False
    non_interactive: bool = False


class ToolSelectionError(ValueError):
    """Raised when pg init cannot safely select an integration."""


def _is_ci() -> bool:
    return os.environ.get("CI", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _choice_prompt(
    candidates: tuple[ToolCandidate, ...],
    input_fn: Callable[[str], str],
    output: Callable[[str], None],
) -> str:
    candidate_ids = {candidate.tool_id for candidate in candidates}
    ordered_ids = [candidate.tool_id for candidate in candidates]
    ordered_ids.extend(tool for tool in supported_tools() if tool not in candidate_ids)

    output("Select a development-tool integration:")
    for index, tool_id in enumerate(ordered_ids, start=1):
        integration = get_integration(tool_id)
        detected = next(
            (candidate for candidate in candidates if candidate.tool_id == tool_id),
            None,
        )
        suffix = " [detected]" if detected else ""
        output(f"  {index}. {integration.display_name} ({tool_id}){suffix}")

    while True:
        answer = input_fn(f"Choice [1-{len(ordered_ids)} or tool id]: ").strip()
        if answer.isdigit():
            index = int(answer)
            if 1 <= index <= len(ordered_ids):
                return ordered_ids[index - 1]
        try:
            return normalize_tool_id(answer)
        except ValueError:
            output("Invalid choice. Enter a listed number or tool id.")


def select_tool(
    project_root: Path,
    pg_skills_root: Path,
    options: InitOptions,
    *,
    input_fn: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
    input_is_tty: bool | None = None,
) -> str:
    """Resolve the requested tool using explicit selection or safe detection."""

    if options.tool:
        try:
            return normalize_tool_id(options.tool)
        except ValueError as exc:
            raise ToolSelectionError(str(exc)) from exc

    is_tty = sys.stdin.isatty() if input_is_tty is None else input_is_tty
    if options.non_interactive or _is_ci() or not is_tty:
        raise ToolSelectionError(
            "No --tool was provided in a non-interactive session. "
            f"Pass one explicitly: --tool {{{', '.join(supported_tools())}}}"
        )

    detected = detect_tools(project_root, pg_skills_root)
    if detected:
        highest = detected[0].confidence
        strongest = tuple(item for item in detected if item.confidence == highest)
    else:
        strongest = ()

    if len(strongest) == 1:
        candidate = strongest[0]
        output(
            f"Detected {candidate.display_name} ({candidate.tool_id}): "
            + "; ".join(candidate.reasons)
        )
        if options.assume_yes:
            return candidate.tool_id
        answer = input_fn(f"Configure {candidate.display_name}? [Y/n]: ").strip().lower()
        if answer in {"", "y", "yes"}:
            return candidate.tool_id

    if len(strongest) > 1:
        output("Multiple development tools were detected:")
        for candidate in strongest:
            output(
                f"  - {candidate.display_name} ({candidate.tool_id}): "
                + "; ".join(candidate.reasons)
            )
    elif not strongest:
        output("No development tool could be identified reliably.")

    return _choice_prompt(strongest, input_fn, output)


def initialize_project(
    project_root: Path,
    pg_skills_root: Path,
    options: InitOptions,
    *,
    input_fn: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
    input_is_tty: bool | None = None,
) -> int:
    """Create the pg-skills project skeleton and install one tool adapter."""

    if options.list_tools:
        output("Supported development tools:")
        for tool_id in supported_tools():
            integration = get_integration(tool_id)
            output(f"  {tool_id:<14} {integration.display_name}")
        return 0

    project_root = project_root.resolve()
    pg_skills_root = pg_skills_root.resolve()
    pg_dir = project_root / ".pg"

    if not pg_dir.exists():
        output(f"ERROR: .pg/ not found at {pg_dir}.")
        output("  .pg/skills must be present before running pg init.")
        output("  If pg-skills has not been synced, run:")
        output("    git subtree add --prefix=.pg/skills pg-skills v0.9.1 --squash")
        return 1

    integration = None
    if not options.skip_tool_config:
        try:
            selected_tool = select_tool(
                project_root,
                pg_skills_root,
                options,
                input_fn=input_fn,
                output=output,
                input_is_tty=input_is_tty,
            )
            integration = get_integration(selected_tool)
        except ToolSelectionError as exc:
            output(f"ERROR: {exc}")
            return 2

    ensure_project_skeleton(project_root)

    integration_result = None
    if integration is not None:
        integration_result = integration.install(
            IntegrationContext(project_root, pg_skills_root)
        )
        save_selected_tool(project_root, integration.tool_id)

    create_pg_run_wrappers(project_root, output=output)
    version_path = pg_skills_root / "VERSION"
    version = (
        version_path.read_text(encoding="utf-8").strip()
        if version_path.exists()
        else "unknown"
    )

    output(f"pg-skills initialized in {project_root}")
    output(f"  - pg-skills version: {version}")
    if integration is not None and integration_result is not None:
        output(
            f"  - tool integration: {integration.display_name} "
            f"({integration.tool_id})"
        )
        for message in integration_result.messages:
            output(f"    - {message}")
        for warning in integration_result.warnings:
            output(f"    WARN: {warning}")
    else:
        output("  - tool integration skipped")

    if integration is not None:
        output("")
        output("Next steps:")
        for index, step in enumerate(integration.next_steps(), start=1):
            output(f"  {index}. {step}")
    return 0


def refresh_configured_integration(
    project_root: Path,
    pg_skills_root: Path,
    *,
    output: Callable[[str], None] = print,
) -> None:
    """Refresh the adapter selected by the last successful initialization."""

    tool_id = load_selected_tool(project_root, default=None)
    if not tool_id:
        raise ToolSelectionError(
            "No configured development-tool integration. Run `pg init` "
            "or pass `pg init --tool <tool-id>` first."
        )
    integration = get_integration(tool_id)
    result = integration.install(
        IntegrationContext(project_root, pg_skills_root)
    )
    output(f"Refreshing {integration.display_name} integration:")
    for message in result.messages:
        output(f"  - {message}")
    for warning in result.warnings:
        output(f"  WARN: {warning}")


def create_pg_run_wrappers(
    project_root: Path,
    *,
    output: Callable[[str], None] = print,
) -> None:
    """Expose pg-run without requiring Windows Developer Mode."""

    link = project_root / "pg-run"
    target = ".pg/skills/src/runtime/bin/pg-run"

    if os.name == "nt":
        if link.is_symlink():
            link.unlink()
        link.write_text(
            '#!/usr/bin/env bash\npython .pg/skills/src/runtime/bin/pg-run "$@"\n',
            encoding="utf-8",
        )
        cmd = project_root / "pg-run.cmd"
        cmd.write_text(
            '@echo off\r\npython ".pg\\skills\\src\\runtime\\bin\\pg-run" %*\r\n',
            encoding="utf-8",
        )
        output("  - wrappers: pg-run + pg-run.cmd")
        return

    target_resolved = (project_root / target).resolve()
    if link.is_symlink():
        if link.resolve() == target_resolved:
            return
        link.unlink()
    elif link.exists():
        link.unlink()
    link.symlink_to(target)
    link.chmod(link.stat().st_mode | 0o111)
    output(f"  - symlink: pg-run -> {target}")


def ensure_project_skeleton(project_root: Path) -> None:
    pg_dir = project_root / ".pg"
    for subdir in ("hooks", "context", "scripts", "changes", "runs"):
        (pg_dir / subdir).mkdir(exist_ok=True)
    pg_gitignore = pg_dir / ".gitignore"
    if not pg_gitignore.exists():
        pg_gitignore.write_text(GITIGNORE_CONTENT, encoding="utf-8")
    changes_gitignore = pg_dir / "changes" / ".gitignore"
    if not changes_gitignore.exists():
        changes_gitignore.write_text(CHANGES_GITIGNORE_CONTENT, encoding="utf-8")
    project_yaml = pg_dir / "project.yaml"
    if not project_yaml.exists():
        project_yaml.write_text(
            generate_project_yaml(project_root), encoding="utf-8"
        )


def generate_project_yaml(project_root: Path) -> str:
    name = project_root.name
    return f"""# pg-skills project declaration
# Edit this file to declare real modules, environments, tracks, and stages.
# Schema: .pg/skills/src/runtime/spec/project.schema.json

schema: spec-driven
# project: name={name}

modules:
  placeholder:
    root: .
    language: python
    description: "Placeholder module; replaced by pg-init-project."
environments:
  placeholder:
    description: "Placeholder environment; replace during project onboarding."
    roles:
      placeholder:
        instances:
          - name: placeholder-1
            host: localhost
            port: 9999
tracks:
  placeholder:
    modules: [placeholder]
    max_fail_retries: 1
    max_fix_retries: 1
    description: "Placeholder track; replace after defining real modules."
stages:
  - name: placeholder
    tracks: [placeholder]
    gate: all_pass
    environment:
      required: false
    description: "Placeholder stage; replace after defining real modules."
"""
