# Codex Workflow Governor

Codex Workflow Governor compiles repository workflow sources into immutable runtime locks and generated views. It provides explicit skills, separate reader and maintainer MCP surfaces, Codex hooks for permit-bound subagent dispatch, and an adaptive documentation-quality skill.

## Status

Version 1 targets the current Linux Codex runtime and uses only the Python standard library. It does not depend on LangGraph, CrewAI, Temporal, or a web interface.

## Repository Layout

- `.codex-plugin/plugin.json` defines the plugin.
- `skills/` contains workflow lifecycle, methodology, and documentation-governance skills.
- `workflow_governor/` contains the compiler, contracts, ledger, MCP server, and hook implementation.
- `scripts/` contains command-line entry points.
- `hooks/hooks.json` registers bundled lifecycle hooks.

## Runtime Model

Source graphs live in target repositories under `.codex/workflows/<workflow-id>/workflow.json`. Apply creates `workflow.lock.json`, `workflow.mmd`, role TOML files, and `WORKFLOW.md` atomically. Run state and drafts stay under `PLUGIN_DATA` and are never committed to target repositories.

Guarded execution is available only when trusted hooks and native dispatch inputs are observable. Otherwise the runtime records the run as `advisory` and never claims graph compliance.

## Explicit Skills

The plugin exposes `workflow-create`, `workflow-check`, `workflow-analyze`, `workflow-update`, `workflow-apply`, `workflow-visualize`, and `workflow-run` for the workflow lifecycle.

It also exposes `adaptive-deepening` and `graph-completion` as methodology skills for evidence-wave enrichment and knowledge-graph gap completion.

A Governor workflow graph coordinates execution. A task-owned knowledge graph stores domain entities and facts; `graph-completion` never treats those two graph types as the same state.

`score-documentation-quality` handles documentation edits, evidence-backed 100-point scoring, and lossless instruction migration. It selects the minimum sufficient mode and may trigger implicitly for relevant documentation work.

Workflow lifecycle and methodology skills disable implicit invocation. Drafting and updating never modify the target repository; only `workflow-apply` materializes a draft.

The Workflow Governor runtime never runs Git commands and never creates Git policy. The documentation skill may publish only when the user authorizes it and the applicable repository instructions permit it.
