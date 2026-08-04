# Development-tool integrations

pg-skills separates the workflow contract from development-tool APIs.

## Layers

`src/core/workflows/` is the canonical workflow pack. It may describe
tool-dependent operations only through explicit tokens such as:

- `{{pg:action.skill_loader}}`
- `{{pg:action.subagent_dispatcher}}`
- `{{pg:action.user_question}}`
- `{{pg:permission.subagent}}`
- `{{pg:model.expert}}`

It must not name a concrete tool API such as OpenCode `Task tool` or a
tool-specific model route.

`src/integrations/<tool>/templates/integration.json` maps every token to that
tool's vocabulary. `adapter.py` installs rendered commands, agents, and skills
in the tool's project directory. The pipeline runner, event log, reducer,
hooks, and `.pg/project.yaml` remain shared.

## Initialization

```text
pg init --tool opencode
pg init --tool mobile-coder
```

When `--tool` is omitted in an interactive terminal, `pg init` detects project
markers, environment variables, and executables. It asks for confirmation when
one tool is found and asks the user to choose when several tools are found.
Non-interactive use requires `--tool`.

## Adding a tool

1. Add `src/integrations/<tool>/adapter.py`.
2. Add `templates/integration.json` with every workflow token mapped.
3. Register the adapter in `src/integrations/registry.py`.
4. Add detection markers and executable names.
5. Add installation, rendering, permission, and idempotency tests.
6. Verify that generated files contain no unresolved `{{pg:...}}` tokens.

An adapter may change paths, configuration shape, permissions, and invocation
syntax. It must not change pipeline ordering, failure handling, result
recording, verification, merge, or archive semantics.
