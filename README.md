# Codex Workflow Governor

Codex Workflow Governor compiles repository workflow sources into immutable runtime locks and generated views. It provides explicit skills, separate reader and maintainer MCP surfaces, Codex hooks for permit-bound subagent dispatch, and an adaptive documentation-quality skill.

## Status

Version 1 targets the current Linux Codex runtime and uses only the Python standard library. It does not depend on LangGraph, CrewAI, Temporal, or a web interface.

## Repository Layout

- `.codex-plugin/plugin.json` defines the plugin.
- `skills/` contains workflow lifecycle skills and `score-documentation-quality`.
- `workflow_governor/` contains the compiler, contracts, ledger, MCP server, and hook implementation.
- `scripts/` contains command-line entry points.
- `hooks/hooks.json` registers bundled lifecycle hooks.

## Runtime Model

Source graphs live in target repositories under `.codex/workflows/<workflow-id>/workflow.json`. Apply creates `workflow.lock.json`, `workflow.mmd`, role TOML files, and `WORKFLOW.md` atomically. Run state and drafts stay under `PLUGIN_DATA` and are never committed to target repositories.

Guarded execution is available only when trusted hooks and native dispatch inputs are observable. Otherwise the runtime records the run as `advisory` and never claims graph compliance.

## Explicit Skills

The plugin exposes `workflow-create`, `workflow-check`, `workflow-analyze`, `workflow-update`, `workflow-apply`, `workflow-visualize`, `workflow-run`, and `score-documentation-quality`. Workflow lifecycle skills disable implicit invocation. `score-documentation-quality` may trigger for relevant documentation work and selects the minimum sufficient mode: quick edit, formal scoring, or deep migration.

The Workflow Governor runtime never runs Git commands and never creates Git policy. The documentation skill may publish only when the user authorizes it and the applicable repository instructions permit it.
