# Changelog

All notable changes to pg-skills are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Unified hook command executor**: `src/runtime/lib/pg-run-hook.py` — single
  entry point for env hooks (prepare_env / clean_env) and role actions
  (start / stop / logs / tail). Reads JSON spec from stdin, injects PG_*
  protocol env vars (PG_PROJECT_ROOT / PG_SKILLS_PATH / PG_CHANGE_NAME /
  PG_STAGE / PG_ENV / PG_ROLE / PG_INSTANCE_NAME / PG_INSTANCE_HOST /
  PG_HOOK_TYPE), runs the command with timeout, returns JSON result.
  Module hooks (build / lint / test.<key>) stay as raw `timeout N bash -c
  '<cmd>'` strings so agents keep flexibility to run individual tests.

### Changed
- **Breaking**: `pg-regression/scripts/pg-run-command.py` merged into
  `pg-run-hook.py` and deleted. All references updated. The new
  `timeout_seconds` field replaces the legacy `timeout` field.
- **Breaking**: `stage.environment.actions` keys are now flattened to
  `role.<role>.<action>@<instance_name>` (e.g. `role.backend.start@backend-1`).
  Each value has a pre-rendered `cmd` field (full `pg-run-hook.py` invocation);
  sub-agents must `bash {cmd}` instead of `bash {script} {args}`.
  Instances with no declared name still get the un-suffixed key for
  backward-compat.
- pg-build runner's `_execute_phase` now wraps env hooks in
  `pg-run-hook.py` instead of executing them with `bash` directly.
- `start-services.sh` (pg-regression) now invokes the new
  `pg-run-hook.py` and uses `timeout_seconds` in its JSON spec.

## [0.1.0] - 2026-06-22

### Added
- Initial extraction of pg-* skills, commands, and agents from webvirt project
- 13 skills: pg-propose, pg-build, pg-quick-build, pg-fix-issue, pg-regression, pg-archive, pg-verify-and-merge, pg-propose-refine, pg-browser-testing-with-devtools, pg-systematic-diagnosing, git-workflow-and-versioning, security-and-hardening, using-agent-skills
- 8 slash commands: /1-pg-define, /2-pg-propose, /2b-pg-quick-build, /2.1-pg-propose-refine, /3-pg-build, /4-pg-regression, /5-pg-fix-issue, /6-pg-archive
- 5 sub-agents: explore, pg-manager, pg-build/{dev,test,verify,fix,fix-gate,gate}, pg-fix-issue/{executor,fix-and-pr}, pg-regression/fix-test, pg-quick-build/worker
- L1 runtime skeleton: src/runtime/{bin,lib,spec} (structure only, content in Phase 2)
- 3 language example templates: java-maven, go, typescript (structure only)

### Notes
- This is a "skeleton + de-webvirtified" release
- Python test fixtures have been generalized with `<module-name>` placeholders
- Full hook protocol implementation arrives in 0.2.0
- Full `pg` CLI implementation arrives in 0.2.0