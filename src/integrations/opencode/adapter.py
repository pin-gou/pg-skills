"""OpenCode adapter rendered from the canonical tool-neutral workflow pack."""

from __future__ import annotations

import hashlib
import json
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


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class OpenCodeIntegration(ToolIntegration):
    tool_id = "opencode"
    display_name = "OpenCode"
    aliases = ("open-code",)
    project_markers = (".opencode", "opencode.json", "opencode.jsonc")
    environment_markers = ("OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR")
    executables = ("opencode",)

    def install(self, context: IntegrationContext) -> IntegrationResult:
        source_root = context.workflow_root
        descriptor = self.descriptor()
        opencode_root = context.project_root / descriptor["output_root"]
        result = IntegrationResult()
        surfaces = descriptor["surfaces"]

        _remove_legacy_links(opencode_root, source_root, surfaces, result, output_label=".opencode")

        generated: dict[Path, tuple[bytes, int]] = {}
        variables = self.template_variables()
        for source_name, target_name in surfaces.items():
            source_dir = source_root / source_name
            if not source_dir.exists():
                result.warnings.append(f"{source_dir} not found; skipped")
                continue
            generated.update(
                collect_rendered_files(source_dir, Path(target_name), variables, TEXT_EXTENSIONS)
            )

        manifest_path = opencode_root / MANIFEST_NAME
        previous: dict = {}
        if manifest_path.exists():
            try:
                previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                result.warnings.append(f"ignored invalid {MANIFEST_NAME}")
        previous_files = previous.get("files", {})

        opencode_root.mkdir(parents=True, exist_ok=True)
        written_hashes: dict[str, str] = {}
        for relative, (data, mode) in generated.items():
            target = opencode_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            relative_key = relative.as_posix()
            # Silently replace any symlink that still shadows a rendered file
            # (dangling, out-of-tree, or user-created). Rendered surfaces are
            # owned by pg-skills, so a symlink at this path is always replaced.
            if target.is_symlink():
                target.unlink()
            old_hash = previous_files.get(relative_key)
            if target.exists() and old_hash:
                if _sha256(target.read_bytes()) != old_hash:
                    result.warnings.append(
                        f"preserved modified file: .opencode/{relative_key}"
                    )
                    written_hashes[relative_key] = old_hash
                    continue
            elif target.exists() and relative_key not in previous_files:
                result.warnings.append(
                    f"preserved untracked file: .opencode/{relative_key}"
                )
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
            stale = opencode_root / Path(relative_key)
            if stale.is_file() and _sha256(stale.read_bytes()) == old_hash:
                stale.unlink()
                result.messages.append(f"removed stale .opencode/{relative_key}")
            elif stale.exists():
                result.warnings.append(
                    f"preserved modified stale file: .opencode/{relative_key}"
                )

        manifest = {
            "schema_version": 1,
            "tool": self.tool_id,
            "source": ".pg/skills/src/core/workflows",
            "files": dict(sorted(written_hashes.items())),
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        result.messages.extend(
            f"rendered .opencode/{name}" for name in surfaces.values()
        )
        return result

    def next_steps(self) -> list[str]:
        return [
            "Restart OpenCode so it reloads project commands, skills, and agents.",
            "Load the pg-init-project skill to scan and configure the project.",
        ]
