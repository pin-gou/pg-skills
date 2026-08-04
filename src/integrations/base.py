"""Shared contracts for pg-skills development-tool integrations."""

from __future__ import annotations

import json
import inspect
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path


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
