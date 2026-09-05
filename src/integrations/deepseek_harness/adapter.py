"""Project-local DeepSeek Harness adapter for the canonical workflow pack."""

from __future__ import annotations

import hashlib
import json
import os
import re
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
HARNESS_CONTRACT = """## DeepSeek Harness execution contract

- Skills are loaded through the native Skill loader from `.dsh/skills/`.
- Use `ask_user_question` for user decisions and `todo_write` for task tracking.
- A runner `dispatch` action names a pg role such as `pg-build/test`. Read the
  matching `.dsh/agents/<role>.md`, inspect its `model` frontmatter, then use the
  corresponding native routed subagent tool: `pg_associate` for
  `pg-router/pg-associate`, `pg_expert` for `pg-router/pg-expert`, or `pg_master`
  for `pg-router/pg-master`. Those tool names are stable workflow tiers; their
  actual DSH provider/model mappings are configured in .dsh/cordis.patch.yml.
  Put the complete role document and runner dispatch
  prompt in `prompt`; keep `description` to a short 3-5 word summary.
- After the subagent returns, pass its result to the pg runner `record` action
  exactly as the workflow requires. The native subagent tool does not accept
  an OpenCode-style agent-id parameter.
- Treat runner action `done` as a transition. Per the v0.9.2 breaking change,
  pg-build no longer auto-loads `pg-verify-and-merge` after `done`. Stop and
  wait for the user to explicitly request verification/merge (e.g. "verify
  并合并") before loading the skill.
"""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _adapt_text(text: str) -> str:
    text = re.sub(r"^model:\s*current\r?\n", "", text, flags=re.MULTILINE)
    replacements = (
        (".pg/skills/src/core/workflows/skills/", ".dsh/skills/"),
        (".pg/skills/src/core/workflows/agents/", ".dsh/agents/"),
        (".opencode/skills/", ".dsh/skills/"),
        (".opencode/agents/", ".dsh/agents/"),
        (".opencode/", ".dsh/"),
        ("python3", "python" if os.name == "nt" else "python3"),
    )
    for source, target in replacements:
        text = text.replace(source, target)
    return text


def _adapt_tree(generated: dict[Path, tuple[bytes, int]], *, add_contract: bool) -> None:
    for relative, (data, mode) in list(generated.items()):
        text = _adapt_text(data.decode("utf-8"))
        if add_contract and relative.suffix == ".md":
            text = f"{text.rstrip()}\n\n{HARNESS_CONTRACT}\n"
        generated[relative] = (text.encode("utf-8"), mode)


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    _, block, *_ = text.split("---", 2)
    result: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip()
    return result


def _harness_command_name(name: str) -> str:
    """Convert numbered pg command names to DeepSeek Harness-safe names."""
    if re.fullmatch(r"[a-z][a-z0-9_-]*", name):
        return name
    numbered = re.fullmatch(r"(\d+[a-z]?)-pg-(.+)", name)
    if numbered:
        return f"pg-{numbered.group(1)}-{numbered.group(2)}"
    return f"pg-{name}"


def _command_bridge(commands: dict[Path, tuple[bytes, int]]) -> str:
    definitions = []
    for relative, (data, _mode) in sorted(commands.items()):
        if relative.suffix != ".md":
            continue
        metadata = _frontmatter(data.decode("utf-8"))
        name = metadata.get("name")
        if not name:
            continue
        definitions.append(
            {
                "name": _harness_command_name(name),
                "description": metadata.get("description", f"Run {name}"),
                "document": f"commands/{relative.name}",
                "agent": metadata.get("agent", ""),
            }
        )

    encoded = json.dumps(definitions, ensure_ascii=False, indent=2)
    return f"""import {{ readFileSync }} from 'node:fs'
import {{ dirname, join }} from 'node:path'
import {{ fileURLToPath }} from 'node:url'

export const name = 'pg-skills-project-commands'
export const inject = ['commands']

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const COMMANDS = {encoded} as const

function createUserMessage(text: string) {{
  return Object.freeze({{
    id: crypto.randomUUID(),
    role: 'user',
    content: [{{ type: 'text', text }}],
    source: {{ kind: 'user' }},
  }})
}}

function run(definition: (typeof COMMANDS)[number], invocation: any) {{
  const command = readFileSync(join(ROOT, definition.document), 'utf8')
  const role = definition.agent
    ? readFileSync(join(ROOT, 'agents', `${{definition.agent}}.md`), 'utf8')
    : ''
  const prompt = [
    'Execute this project-local pg-skills command in DeepSeek Harness.',
    'Follow the command and role documents exactly; do not merely summarize them.',
    `Command arguments: ${{invocation.rawInput || '(none)'}}`,
    role ? `Primary role document:\n${{role}}` : '',
    `Command document:\n${{command}}`,
  ].filter(Boolean).join('\\n\\n')

  invocation.agent.followup(createUserMessage(prompt))
  return {{ kind: 'success', text: `Started /${{definition.name}}` }}
}}

export function apply(ctx: any): void {{
  for (const definition of COMMANDS) {{
    ctx.commands.register({{
      name: definition.name,
      description: definition.description,
      input: {{ hint: '<arguments>' }},
      handler: invocation => run(definition, invocation),
    }})
  }}
}}
"""


def _split_model_route(route: str) -> tuple[str, str]:
    """Split a canonical provider/model route for the DSH agent options."""
    provider, separator, model = route.partition("/")
    if not separator or not provider or not model:
        raise ValueError(f"invalid model route: {route!r}")
    return provider, model


def _cordis_patch(harness_root: Path, variables: dict[str, str]) -> str:
    bridge_uri = (harness_root / "bridge" / "index.ts").resolve().as_uri()
    lines = [
        "- insert:",
        "    - id: pg-skills-project-commands",
        f"      name: '{bridge_uri}'",
        "    - id: pg-skills-model-routes",
        "      name: cordis:group",
        "      group: true",
        "      isolate:",
        "        workflowEngine: true",
        "      config:",
    ]
    for tier in ("associate", "expert", "master"):
        route = variables[f"model.{tier}"]
        provider, model = _split_model_route(route)
        lines.extend(
            (
                f"        - id: pg-subagent-{tier}",
                "          name: '@deepseek-ai/dsh-tool-subagent'",
                "          config:",
                "            provider: spawn",
                f"            toolName: pg_{tier}",
                "            backgroundMode: continuable",
                "            agentOptions:",
                f"              provider: {provider}",
                f"              model: {model}",
            )
        )
    return "\n".join(lines) + "\n"


def _install_managed_tree(
    root: Path,
    generated: dict[Path, tuple[bytes, int]],
    *,
    tool: str,
    source: str,
    label: str,
    result: IntegrationResult,
) -> None:
    manifest_path = root / MANIFEST_NAME
    previous: dict = {}
    if manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            result.warnings.append(f"ignored invalid {label}/{MANIFEST_NAME}")
    previous_files = previous.get("files", {})

    root.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for relative, (data, mode) in generated.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        key = relative.as_posix()
        if target.is_symlink():
            target.unlink()
        old_hash = previous_files.get(key)
        if target.exists() and old_hash and _sha256(target.read_bytes()) != old_hash:
            result.warnings.append(f"preserved modified file: {label}/{key}")
            written[key] = old_hash
            continue
        if target.exists() and key not in previous_files:
            result.warnings.append(f"preserved untracked file: {label}/{key}")
            continue
        target.write_bytes(data)
        try:
            target.chmod(mode)
        except OSError:
            pass
        written[key] = _sha256(data)

    for key, old_hash in previous_files.items():
        if key in written:
            continue
        stale = root / Path(key)
        if stale.is_file() and _sha256(stale.read_bytes()) == old_hash:
            stale.unlink()
            result.messages.append(f"removed stale {label}/{key}")
        elif stale.exists():
            result.warnings.append(f"preserved modified stale file: {label}/{key}")

    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tool": tool,
                "source": source,
                "files": dict(sorted(written.items())),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


class DeepSeekHarnessIntegration(ToolIntegration):
    tool_id = "deepseek-harness"
    display_name = "DeepSeek Harness"
    aliases = ("deepseek_harness", "deepseekharness", "dsh")
    project_markers = (".deepseek-harness", ".dsh")
    environment_markers = ("DSH_HOME",)
    executables = ("dsh",)

    def install(self, context: IntegrationContext) -> IntegrationResult:
        source_root = context.workflow_root
        descriptor = self.descriptor()
        harness_root = context.project_root / descriptor["output_root"]
        skill_root = harness_root / "skills"
        result = IntegrationResult()

        required = ("commands", "agents", "skills")
        missing = [name for name in required if not (source_root / name).exists()]
        if missing:
            raise FileNotFoundError(
                f"pg-skills adapter sources missing: {', '.join(missing)}"
            )

        _remove_legacy_links(
            harness_root,
            source_root,
            descriptor["surfaces"],
            result,
            output_label=".dsh",
        )

        variables = self.template_variables()
        commands = collect_rendered_files(
            source_root / "commands", Path("commands"), variables, TEXT_EXTENSIONS
        )
        agents = collect_rendered_files(
            source_root / "agents", Path("agents"), variables, TEXT_EXTENSIONS
        )
        skills = collect_rendered_files(
            source_root / "skills", Path("skills"), variables, TEXT_EXTENSIONS
        )
        _adapt_tree(commands, add_contract=True)
        _adapt_tree(agents, add_contract=True)
        _adapt_tree(skills, add_contract=True)

        harness_files = {**commands, **agents, **skills}
        harness_files[Path("bridge/index.ts")] = (
            _command_bridge(commands).encode("utf-8"),
            0o644,
        )
        patch = _cordis_patch(harness_root, variables)
        harness_files[Path("cordis.patch.yml")] = (patch.encode("utf-8"), 0o644)
        start_web_cmd = (
            '@echo off\r\n'
            'setlocal\r\n'
            'pushd "%~dp0.." >nul\r\n'
            'dsh --profile web --patch "%~dp0cordis.patch.yml" %*\r\n'
            'set "PG_DSH_EXIT=%ERRORLEVEL%"\r\n'
            'popd >nul\r\n'
            'exit /b %PG_DSH_EXIT%\r\n'
        )
        harness_files[Path("start-web.cmd")] = (
            start_web_cmd.encode("utf-8"),
            0o755,
        )
        run_task_cmd = (
            '@echo off\r\n'
            'setlocal\r\n'
            'if "%~1"=="" (\r\n'
            '  echo Usage: .dsh\\run-task.cmd "task"\r\n'
            '  exit /b 2\r\n'
            ')\r\n'
            'pushd "%~dp0.." >nul\r\n'
            'dsh --profile headless --patch "%~dp0cordis.patch.yml" %*\r\n'
            'set "PG_DSH_EXIT=%ERRORLEVEL%"\r\n'
            'popd >nul\r\n'
            'exit /b %PG_DSH_EXIT%\r\n'
        )
        harness_files[Path("run-task.cmd")] = (
            run_task_cmd.encode("utf-8"),
            0o755,
        )
        run_cmd = (
            '@echo off\r\n'
            'echo DeepSeek Harness project launchers:\r\n'
            'echo   Interactive web: .dsh\\start-web.cmd\r\n'
            'echo   One-shot task:   .dsh\\run-task.cmd "task"\r\n'
        )
        harness_files[Path("run.cmd")] = (run_cmd.encode("utf-8"), 0o755)

        start_web_sh = (
            '#!/usr/bin/env sh\n'
            'DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"\n'
            'cd "$DIR/.." || exit 1\n'
            'exec dsh --profile web --patch "$DIR/cordis.patch.yml" "$@"\n'
        )
        harness_files[Path("start-web.sh")] = (
            start_web_sh.encode("utf-8"),
            0o755,
        )
        run_task_sh = (
            '#!/usr/bin/env sh\n'
            'if [ "$#" -eq 0 ]; then\n'
            '  echo \'Usage: .dsh/run-task.sh "task"\'\n'
            '  exit 2\n'
            'fi\n'
            'DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"\n'
            'cd "$DIR/.." || exit 1\n'
            'exec dsh --profile headless --patch "$DIR/cordis.patch.yml" "$@"\n'
        )
        harness_files[Path("run-task.sh")] = (
            run_task_sh.encode("utf-8"),
            0o755,
        )
        run_sh = (
            '#!/usr/bin/env sh\n'
            'echo \'DeepSeek Harness project launchers:\'\n'
            'echo \'  Interactive web: .dsh/start-web.sh\'\n'
            'echo \'  One-shot task:   .dsh/run-task.sh "task"\'\n'
        )
        harness_files[Path("run.sh")] = (run_sh.encode("utf-8"), 0o755)
        readme = """# DeepSeek Harness adapter
Generated by `pg init --tool deepseek-harness`.

- `commands/` contains rendered pg command documents.
- `agents/` contains pg primary-role and subagent role documents.
- `bridge/index.ts` explicitly registers the slash commands with Cordis.
- `cordis.patch.yml` loads only this project-local command bridge.
- `skills/` contains skills discovered by DeepSeek Harness natively.
- `cordis.patch.yml` registers three native subagent tools bound to the
  associate, expert, and master model routes.

Use `start-web.cmd` (`start-web.sh` on Unix) for the interactive browser UI.
Use `run-task.cmd "task"` (`run-task.sh "task"` on Unix) for one Headless task.
`run.cmd` and `run.sh` only display this usage. This adapter does not modify the
user's global DeepSeek Harness configuration. The three routed
subagent tools default to the configured official deepseek-v4-flash model; edit
cordis.patch.yml when distinct associate, expert, and master models are available.
"""
        harness_files[Path("README.md")] = (readme.encode("utf-8"), 0o644)

        _install_managed_tree(
            harness_root,
            harness_files,
            tool=self.tool_id,
            source=".pg/skills/src/core/workflows",
            label=".dsh",
            result=result,
        )

        result.messages.extend(
            (
                f"registered {len(list((harness_root / 'commands').glob('*.md')))} commands",
                f"installed {len(list((harness_root / 'agents').rglob('*.md')))} agent roles",
                f"installed {len(list(skill_root.glob('*/SKILL.md')))} native skills",
                "generated project-local Cordis command and model-route bridge",
                "left global DeepSeek Harness configuration unchanged",
            )
        )
        return result

    def next_steps(self) -> list[str]:
        return [
            "Start the interactive UI with .dsh/start-web.cmd (Windows) or .dsh/start-web.sh (Unix).",
            "For one Headless task, use .dsh/run-task.cmd \"task\" or .dsh/run-task.sh \"task\".",
            "Confirm /0-pg-auto-pilot, /pg-1-define and /pg-3-build are listed as commands.",
            "Ask Harness to load pg-init-project and initialize the project configuration.",
        ]
