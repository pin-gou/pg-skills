"""Mobile Coder adapter rendered from the canonical pg-skills workflow pack."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

from ..base import (
    IntegrationContext,
    IntegrationResult,
    ToolIntegration,
    collect_rendered_files,
    _remove_legacy_links,
)


TEXT_EXTENSIONS = {
    ".md",
    ".py",
    ".sh",
    ".json",
    ".yaml",
    ".yml",
    ".html",
    ".txt",
}
MANIFEST_NAME = ".pg-adapter-manifest.json"
BUILD_COMPLETION_CONTRACT = """Mandatory pg-build completion contract:
- Treat runner action `done` as a transition, not as final command success.
- Report the build result to the user and STOP. Do not auto-load
  `pg-verify-and-merge`: verification and merge happen only when the user
  explicitly requests them (e.g. "verify 并合并").
- Only when the user asks for verification/merge, load and execute the native
  `pg-verify-and-merge` skill, and do not report completion until verification
  and merge succeed, the current branch is the configured default branch, and
  the business changes are committed.
- If an auto-record commit, archive commit, verification, or merge fails,
  report the failure and stop instead of claiming completion."""


def _adapt_text(text: str) -> str:
    """Apply Mobile Coder path and platform adjustments after token rendering."""

    text = re.sub(r"^model:\s*current\r?\n", "", text, flags=re.MULTILINE)
    replacements = (
        (".pg/skills/src/core/workflows/scripts/", ".mobile-coder/runtime/scripts/"),
        (".pg/skills/src/core/workflows/skills/", ".mobile-coder/skills/"),
        (".pg/skills/src/core/workflows/agents/", ".mobile-coder/agents/"),
        (".pg/skills/src/runtime/", ".mobile-coder/pg-skills/src/runtime/"),
        (".opencode/skills/", ".mobile-coder/skills/"),
        (".opencode/agents/", ".mobile-coder/agents/"),
        (".opencode/workflows/", ".mobile-coder/workflows/"),
        ("opencode/agents/", ".mobile-coder/agents/"),
        (".opencode/", ".mobile-coder/"),
        ("python3", "python" if os.name == "nt" else "python3"),
    )
    for source, target in replacements:
        text = text.replace(source, target)

    if os.name == "nt":
        text = text.replace(
            '["bash",',
            '[r"C:\\Program Files\\Git\\bin\\bash.exe",',
        )
    return text


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _adapt_tree(
    generated: dict[Path, tuple[bytes, int]],
) -> None:
    completion_targets = {
        Path("commands/pg-3-build.md"),
        Path("agents/pg-manager.md"),
    }
    for relative, (data, mode) in generated.items():
        text = data.decode("utf-8")
        text = _adapt_text(text)
        if relative in completion_targets:
            text = _append_completion_contract(text)
        generated[relative] = (text.encode("utf-8"), mode)


def _append_completion_contract(text: str) -> str:
    """Keep Mobile Coder's native command/agent execution from stopping early."""

    marker = "## Mobile Coder completion contract"
    if marker in text:
        return text
    return f"{text.rstrip()}\n\n{marker}\n\n{BUILD_COMPLETION_CONTRACT}\n"


def _patch_runtime(generated: dict[Path, tuple[bytes, int]]) -> None:
    replacements = {
        Path("pg-skills/src/runtime/bin/pg-invoke-hook.py"): (
            (
                'return project_root / ".pg" / "skills"',
                'return project_root / ".mobile-coder" / "pg-skills"',
            ),
            (
                '/ ".pg" / "skills" / "src" / "opencode" / "skills"',
                '/ ".mobile-coder" / "skills"',
            ),
        ),
        Path("pg-skills/src/runtime/lib/pg-run-hook.py"): (
            (
                'os.path.join(PROJECT_ROOT, ".pg", "skills")',
                'os.path.join(PROJECT_ROOT, ".mobile-coder", "pg-skills")',
            ),
        ),
    }
    for relative, pairs in replacements.items():
        if relative not in generated:
            continue
        data, mode = generated[relative]
        text = data.decode("utf-8")
        for source, target in pairs:
            text = text.replace(source, target)
        generated[relative] = (text.encode("utf-8"), mode)

    config_parser = Path("runtime/scripts/pg-parse-config.py")
    if config_parser in generated:
        data, mode = generated[config_parser]
        text = data.decode("utf-8").replace(
            "CONFIG_PATH_CANDIDATES = [",
            "CONFIG_PATH_CANDIDATES = [\n"
            '    lambda script_dir: os.path.normpath(os.path.join('
            'script_dir, "../../../.pg/project.yaml")),',
            1,
        )
        generated[config_parser] = (text.encode("utf-8"), mode)


class MobileCoderIntegration(ToolIntegration):
    tool_id = "mobile-coder"
    display_name = "Mobile Coder"
    aliases = ("mobile_coder", "mobilecoder")
    project_markers = (
        ".mobile-coder",
        "mobile-coder.json",
    )
    environment_markers = ("MOBILE_CODER_HOME", "MOBILE_CODER_CONFIG")
    executables = ("mobile", "mobile-coder")

    def install(self, context: IntegrationContext) -> IntegrationResult:
        source_root = context.workflow_root
        runtime_root = context.runtime_root
        descriptor = self.descriptor()
        mobile_root = context.project_root / descriptor["output_root"]
        result = IntegrationResult()

        _remove_legacy_links(
            mobile_root,
            source_root,
            descriptor["surfaces"],
            result,
            output_label=".mobile-coder",
        )

        required = ("commands", "agents", "skills", "scripts")
        missing = [name for name in required if not (source_root / name).exists()]
        if missing or not runtime_root.exists():
            paths = ", ".join(missing or ["src/runtime"])
            raise FileNotFoundError(f"pg-skills adapter sources missing: {paths}")

        generated: dict[Path, tuple[bytes, int]] = {}
        variables = self.template_variables()
        generated.update(
            collect_rendered_files(source_root / "commands", Path("commands"), variables, TEXT_EXTENSIONS)
        )
        generated.update(
            collect_rendered_files(source_root / "agents", Path("agents"), variables, TEXT_EXTENSIONS)
        )
        generated.update(
            collect_rendered_files(source_root / "skills", Path("skills"), variables, TEXT_EXTENSIONS)
        )
        generated.update(
            collect_rendered_files(
                source_root / "scripts", Path("runtime/scripts"), variables, TEXT_EXTENSIONS
            )
        )
        generated.update(
            collect_rendered_files(
                runtime_root,
                Path("pg-skills/src/runtime"),
                variables,
                TEXT_EXTENSIONS,
                render_tokens=False,
            )
        )
        # The generic pg CLI owns adapter selection and imports src/integrations.
        # Mobile Coder only needs the pipeline/hook runtime, not a nested CLI copy.
        generated.pop(Path("pg-skills/src/runtime/bin/pg"), None)
        _adapt_tree(generated)
        _patch_runtime(generated)

        readme = """# Mobile Coder adapter

This directory is generated by `pg init --tool mobile-coder`.

- `commands/`: Mobile Coder slash-command documents.
- `agents/`: Mobile Coder primary and subagent definitions.
- `skills/`: Mobile Coder-discoverable pg-skills definitions.
- `runtime/`: platform-facing helper scripts.
- `pg-skills/src/runtime/`: adapted pg-skills runtime.

The upstream pg-skills source remains under `.pg/skills`. Re-run the init
command after upgrading pg-skills. Project-specific files that are not listed
in `.pg-adapter-manifest.json` are preserved. Mobile Coder discovers commands,
agents, and skills directly from this directory; the adapter never creates or
modifies `mobile-coder.json`.
"""
        generated[Path("README.md")] = (readme.encode("utf-8"), 0o644)

        previous_manifest_path = mobile_root / MANIFEST_NAME
        previous: dict = {}
        if previous_manifest_path.exists():
            try:
                previous = json.loads(previous_manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                result.warnings.append(f"ignored invalid {MANIFEST_NAME}")
        previous_files = previous.get("files", {})

        mobile_root.mkdir(parents=True, exist_ok=True)
        written_hashes: dict[str, str] = {}
        for relative, (data, mode) in generated.items():
            target = mobile_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            relative_key = relative.as_posix()
            # Silently replace any symlink that still shadows a rendered file
            # (dangling, out-of-tree, or user-created). Rendered surfaces are
            # owned by pg-skills, so a symlink at this path is always replaced.
            if target.is_symlink():
                target.unlink()
            old_hash = previous_files.get(relative_key)
            if target.exists() and old_hash:
                current_hash = _sha256(target.read_bytes())
                if current_hash != old_hash:
                    result.warnings.append(f"preserved modified file: .mobile-coder/{relative_key}")
                    # Keep the adapter's expected hash in the manifest. Recording
                    # the user's hash would make the next init treat the custom
                    # file as unmodified and overwrite it.
                    written_hashes[relative_key] = old_hash
                    continue
            elif target.exists() and relative_key not in previous_files:
                result.warnings.append(f"preserved untracked file: .mobile-coder/{relative_key}")
                continue

            target.write_bytes(data)
            try:
                target.chmod(mode)
            except OSError:
                pass
            written_hashes[relative_key] = _sha256(data)

        for relative_key, old_hash in previous_files.items():
            if relative_key in written_hashes:
                continue
            if relative_key == "mobile-coder.json":
                result.warnings.append(
                    "preserved legacy mobile-coder.json; remove old pg-skills "
                    "command/agent entries manually after confirming native discovery"
                )
                continue
            stale = mobile_root / Path(relative_key)
            if stale.is_file() and _sha256(stale.read_bytes()) == old_hash:
                stale.unlink()
                result.messages.append(f"removed stale .mobile-coder/{relative_key}")
            elif stale.exists():
                result.warnings.append(f"preserved modified stale file: .mobile-coder/{relative_key}")

        manifest = {
            "schema_version": 1,
            "tool": self.tool_id,
            "source": ".pg/skills/src/core/workflows",
            "files": dict(sorted(written_hashes.items())),
        }
        previous_manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        result.messages.extend(
            (
                f"installed {len(list((mobile_root / 'commands').rglob('*.md')))} commands",
                f"installed {len(list((mobile_root / 'agents').rglob('*.md')))} agents",
                f"installed {len(list((mobile_root / 'skills').glob('*/SKILL.md')))} skills",
                "left mobile-coder.json unchanged",
            )
        )
        return result

    def next_steps(self) -> list[str]:
        return [
            "Restart Mobile Coder so it reloads the project adapter.",
            "Open /skills and confirm pg-init-project and pg-build are listed.",
            "Load pg-init-project to scan and configure the project.",
        ]
