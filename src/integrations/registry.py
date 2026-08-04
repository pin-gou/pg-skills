"""Registry, detection, and persisted selection for tool integrations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .base import DetectionResult, IntegrationContext, ToolIntegration
from .mobile_coder import MobileCoderIntegration
from .opencode import OpenCodeIntegration


_INTEGRATIONS: dict[str, ToolIntegration] = {
    OpenCodeIntegration.tool_id: OpenCodeIntegration(),
    MobileCoderIntegration.tool_id: MobileCoderIntegration(),
}
STATE_PATH = Path(".pg") / "tool-integration.json"


@dataclass(frozen=True)
class ToolCandidate:
    tool_id: str
    display_name: str
    confidence: int
    reasons: tuple[str, ...]


def supported_tools() -> tuple[str, ...]:
    return tuple(sorted(_INTEGRATIONS))


def normalize_tool_id(tool_id: str) -> str:
    normalized = tool_id.strip().lower().replace("_", "-")
    for integration in _INTEGRATIONS.values():
        if normalized == integration.tool_id or normalized in integration.aliases:
            return integration.tool_id
    names = ", ".join(supported_tools())
    raise ValueError(f"Unsupported tool {tool_id!r}. Supported: {names}")


def get_integration(tool_id: str) -> ToolIntegration:
    return _INTEGRATIONS[normalize_tool_id(tool_id)]


def detect_tools(project_root: Path, pg_skills_root: Path) -> tuple[ToolCandidate, ...]:
    """Return detected integrations ordered by confidence and stable tool id."""

    context = IntegrationContext(project_root, pg_skills_root)
    detections: dict[str, DetectionResult] = {}

    saved = load_selected_tool(project_root, default=None)
    if saved:
        detections[saved] = DetectionResult(
            tool_id=saved,
            # A previous choice is a preference, not proof that another
            # project integration is absent. Project markers must still be
            # able to produce the multi-tool selection prompt.
            confidence=70,
            reasons=("saved selection: .pg/tool-integration.json",),
        )

    for integration in _INTEGRATIONS.values():
        detected = integration.detect(context)
        if not detected:
            continue
        existing = detections.get(detected.tool_id)
        if existing:
            detections[detected.tool_id] = DetectionResult(
                tool_id=detected.tool_id,
                confidence=max(existing.confidence, detected.confidence),
                reasons=tuple(dict.fromkeys(existing.reasons + detected.reasons)),
            )
        else:
            detections[detected.tool_id] = detected

    return tuple(
        ToolCandidate(
            tool_id=item.tool_id,
            display_name=get_integration(item.tool_id).display_name,
            confidence=item.confidence,
            reasons=item.reasons,
        )
        for item in sorted(
            detections.values(),
            key=lambda value: (-value.confidence, value.tool_id),
        )
    )


def save_selected_tool(project_root: Path, tool_id: str) -> None:
    tool_id = normalize_tool_id(tool_id)
    path = project_root / STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema_version": 1, "tool": tool_id}, indent=2) + "\n",
        encoding="utf-8",
    )


def load_selected_tool(project_root: Path, default: str | None = "opencode") -> str | None:
    path = project_root / STATE_PATH
    if not path.exists():
        return default
    try:
        tool_id = json.loads(path.read_text(encoding="utf-8")).get("tool", default)
    except (OSError, json.JSONDecodeError):
        return default
    try:
        return normalize_tool_id(tool_id) if tool_id else default
    except ValueError:
        return default
