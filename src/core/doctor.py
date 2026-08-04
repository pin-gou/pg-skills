"""Tool-agnostic project validation used by ``pg doctor``."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable


def schema_path(pg_skills_root: Path) -> Path:
    """Return the canonical project schema path."""

    return pg_skills_root / "src" / "runtime" / "spec" / "project.schema.json"


def validate_project_yaml(project_yaml: Path, schema: Path) -> None:
    """Validate the required project configuration structure."""

    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to validate project.yaml") from exc

    data = yaml.safe_load(project_yaml.read_text(encoding="utf-8")) or {}
    try:
        import jsonschema  # type: ignore
    except ImportError:
        for required in ("modules", "environments", "tracks", "stages"):
            if required not in data:
                raise ValueError(f"missing required field: {required}")
        return

    definition = json.loads(schema.read_text(encoding="utf-8"))
    jsonschema.validate(data, definition)


def run_doctor(
    project_root: Path,
    pg_skills_root: Path,
    *,
    output: Callable[[str], None] = print,
) -> int:
    """Validate pg-skills core state without depending on a tool adapter."""

    project_root = project_root.resolve()
    pg_skills_root = pg_skills_root.resolve()
    pg_dir = project_root / ".pg"
    errors: list[str] = []
    warnings: list[str] = []
    passed = 0

    if not pg_dir.is_dir():
        errors.append(".pg/ directory not found. Run: pg init")
    else:
        passed += 1

    project_yaml = pg_dir / "project.yaml"
    if not project_yaml.is_file():
        errors.append(".pg/project.yaml not found")
    else:
        try:
            validate_project_yaml(project_yaml, schema_path(pg_skills_root))
            output("OK: .pg/project.yaml schema valid")
            passed += 1
        except Exception as exc:
            errors.append(f".pg/project.yaml schema invalid: {exc}")

    version_file = pg_skills_root / "VERSION"
    if not version_file.is_file():
        errors.append(".pg/skills/VERSION not found")
    else:
        output(f"OK: pg-skills version: {version_file.read_text(encoding='utf-8').strip()}")
        passed += 1

    hooks_dir = pg_dir / "hooks"
    if hooks_dir.is_dir():
        for hook in hooks_dir.glob("*.sh"):
            if os.name != "nt" and not os.access(hook, os.X_OK):
                warnings.append(f".pg/hooks/{hook.name} is not executable")

        hook_files = list(hooks_dir.glob("*.sh"))
        common = hooks_dir / "lib" / "common.sh"
        if hook_files and not common.is_file():
            warnings.append(
                ".pg/hooks/lib/common.sh is missing; hook path and log routing "
                "cannot use pg_resolve_paths"
            )
        elif common.is_file():
            content = common.read_text(encoding="utf-8")
            if "pg_resolve_paths()" not in content:
                warnings.append(
                    ".pg/hooks/lib/common.sh does not define pg_resolve_paths"
                )
            else:
                output("OK: hook common library contains pg_resolve_paths")
                passed += 1

    protocol = pg_dir / "context" / "agent-protocol.md"
    if not protocol.is_file():
        warnings.append(
            ".pg/context/agent-protocol.md is missing; run pg-init-project"
        )
    else:
        output("OK: .pg/context/agent-protocol.md exists")
        passed += 1

    agents_md = project_root / "AGENTS.md"
    if agents_md.is_file():
        if "agent-protocol" not in agents_md.read_text(encoding="utf-8"):
            warnings.append(
                "AGENTS.md does not reference .pg/context/agent-protocol.md"
            )
        else:
            output("OK: AGENTS.md references agent-protocol")
            passed += 1

    if errors:
        output("")
        output(f"ERRORS ({len(errors)}):")
        for error in errors:
            output(f"  - {error}")
    if warnings:
        output("")
        output(f"WARNINGS ({len(warnings)}):")
        for warning in warnings:
            output(f"  - {warning}")

    if errors:
        return 1
    output(f"OK ({passed} checks passed)")
    return 0
