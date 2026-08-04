"""Render tool-neutral pg-skills workflow templates for a development tool."""

from __future__ import annotations

import re
from collections.abc import Mapping


TOKEN_PATTERN = re.compile(r"\{\{pg:([a-z][a-z0-9_.-]*)\}\}")


class WorkflowRenderError(ValueError):
    """Raised when a workflow references an unknown adapter variable."""


def render_workflow_text(
    text: str,
    variables: Mapping[str, str],
    *,
    source: str = "<workflow>",
) -> str:
    """Replace explicit pg adapter tokens without rewriting arbitrary prose."""

    missing = sorted(
        {
            match.group(1)
            for match in TOKEN_PATTERN.finditer(text)
            if match.group(1) not in variables
        }
    )
    if missing:
        names = ", ".join(missing)
        raise WorkflowRenderError(f"{source}: missing adapter variables: {names}")

    return TOKEN_PATTERN.sub(lambda match: variables[match.group(1)], text)


def unresolved_workflow_tokens(text: str) -> tuple[str, ...]:
    """Return unresolved token names for validation and tests."""

    return tuple(sorted({match.group(1) for match in TOKEN_PATTERN.finditer(text)}))
