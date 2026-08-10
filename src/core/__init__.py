"""Tool-agnostic pg-skills core."""

__all__ = [
    "InitOptions",
    "initialize_project",
    "ensure_project_skeleton",
    "refresh_configured_integration",
    "run_doctor",
]


def __getattr__(name: str):
    """Load CLI-facing APIs lazily so integrations can import core helpers."""

    if name == "run_doctor":
        from .doctor import run_doctor

        return run_doctor
    if name in {
        "InitOptions",
        "initialize_project",
        "ensure_project_skeleton",
        "refresh_configured_integration",
    }:
        from .init import (
            InitOptions,
            initialize_project,
            ensure_project_skeleton,
            refresh_configured_integration,
        )

        return {
            "InitOptions": InitOptions,
            "initialize_project": initialize_project,
            "ensure_project_skeleton": ensure_project_skeleton,
            "refresh_configured_integration": refresh_configured_integration,
        }[name]
    raise AttributeError(name)
