"""Shared contracts for pg-skills development-tool integrations."""

from __future__ import annotations

import json
import inspect
import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path

from core.rendering import render_workflow_text


@dataclass(frozen=True)
class IntegrationContext:
    """Paths available to an integration during installation."""

    project_root: Path
    pg_skills_root: Path

    @property
    def workflow_root(self) -> Path:
        """Canonical workflow pack consumed by every tool adapter."""

        return self.pg_skills_root / "src" / "core" / "workflows"

    @property
    def runtime_root(self) -> Path:
        return self.pg_skills_root / "src" / "runtime"


@dataclass
class IntegrationResult:
    """Reader-facing summary returned by an integration."""

    messages: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DetectionResult:
    """Evidence that a development tool is active for a project."""

    tool_id: str
    confidence: int
    reasons: tuple[str, ...]


class ToolIntegration:
    """Base class implemented by each supported development tool."""

    tool_id = ""
    display_name = ""
    aliases: tuple[str, ...] = ()
    project_markers: tuple[str, ...] = ()
    environment_markers: tuple[str, ...] = ()
    executables: tuple[str, ...] = ()

    @property
    def package_root(self) -> Path:
        return Path(inspect.getfile(self.__class__)).resolve().parent

    @property
    def template_root(self) -> Path:
        return self.package_root / "templates"

    def descriptor(self) -> dict:
        """Load adapter metadata kept beside its implementation."""

        path = self.template_root / "integration.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def template_variables(self) -> dict[str, str]:
        """Return the explicit workflow vocabulary supplied by this adapter."""

        descriptor = self.descriptor()
        variables = descriptor.get("template_variables", {})
        if not isinstance(variables, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in variables.items()
        ):
            raise ValueError(
                f"{self.tool_id}: integration.json template_variables must "
                "be a string mapping"
            )
        return variables

    def detect(self, context: IntegrationContext) -> DetectionResult | None:
        """Detect project, environment, or executable evidence for this tool."""

        reasons: list[str] = []
        confidence = 0
        for marker in self.project_markers:
            if (context.project_root / marker).exists():
                reasons.append(f"project marker: {marker}")
                confidence = max(confidence, 80)

        for variable in self.environment_markers:
            if os.environ.get(variable):
                reasons.append(f"environment variable: {variable}")
                confidence = max(confidence, 90)

        if not reasons:
            for executable in self.executables:
                if shutil.which(executable):
                    reasons.append(f"executable on PATH: {executable}")
                    confidence = max(confidence, 20)
                    break

        if not reasons:
            return None
        return DetectionResult(self.tool_id, confidence, tuple(reasons))

    def install(self, context: IntegrationContext) -> IntegrationResult:
        raise NotImplementedError

    def next_steps(self) -> list[str]:
        return []


EXCLUDED_RENDER_DIRS = frozenset({"tests", "__pycache__", ".pytest_cache"})


def collect_rendered_files(
    source: Path,
    target_prefix: Path,
    variables: dict[str, str],
    text_extensions: set[str],
    *,
    render_tokens: bool = True,
) -> dict[Path, tuple[bytes, int]]:
    generated: dict[Path, tuple[bytes, int]] = {}
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if any(part in EXCLUDED_RENDER_DIRS for part in relative.parts):
            continue
        if relative.name == ".gitignore":
            continue
        data = path.read_bytes()
        if path.suffix.lower() in text_extensions:
            text = data.decode("utf-8")
            if render_tokens:
                text = render_workflow_text(text, variables, source=str(path))
            data = text.encode("utf-8")
        generated[target_prefix / relative] = (
            data,
            stat.S_IMODE(path.stat().st_mode),
        )
    return generated


def _is_legacy_link(entry: Path, source_root: Path) -> bool:
    """Detect a symlink an adapter must replace before writing rendered files.

    Returns True when ``entry`` is a symlink that either:

    - is dangling (``exists()`` is False because the target was removed, e.g.
      a pg-skills checkout that has been moved in-tree), or
    - resolves to a path outside the current workflow root (an out-of-tree
      link left over from an older pg-skills layout).

    Both forms crash the naive ``Path.exists()`` / ``Path.write_bytes()``
    pattern with ``FileNotFoundError`` because Python follows symlinks on
    open. Adapters must unlink these before writing the rendered file.
    """

    if not entry.is_symlink():
        return False
    if not entry.exists():
        return True
    try:
        entry.resolve(strict=True).relative_to(source_root.resolve())
    except (OSError, ValueError):
        return True
    return False


def _remove_legacy_links(
    output_root: Path,
    source_root: Path,
    surfaces: dict[str, str],
    result: IntegrationResult,
    *,
    output_label: str,
) -> None:
    """Replace stale symlinks in every rendered surface before writes.

    Sweeps each ``surfaces`` subdirectory under ``output_root`` and removes
    entries that ``is_legacy_link`` flags (dangling or out-of-tree). Used by
    every adapter that renders a managed surface from the canonical
    workflow pack. User-created symlinks that shadow a generated file are
    removed silently per the adapter contract: rendered surfaces are owned
    by pg-skills.
    """

    for subdir in surfaces.values():
        target_dir = output_root / subdir
        if not target_dir.is_dir():
            continue
        for entry in target_dir.iterdir():
            if _is_legacy_link(entry, source_root):
                entry.unlink()
                result.messages.append(
                    f"migrated legacy {output_label}/{subdir}/{entry.name} link"
                )
