# Security

## Optional lifecycle tool surfaces

This plugin does not register MCP servers or lifecycle hooks. If `workflow-governor`, `workflow-governor-maintainer`, and trusted lifecycle hooks are supplied separately for the explicit lifecycle skills, keep the reader surface read-only and treat the maintainer as an opt-in mutation surface: use it only for a user-requested draft, apply, run transition, result submission, or render operation. Tool annotations identify operations that can overwrite repository or runtime state.

Do not expose the maintainer surface to untrusted MCP clients. Repository files, workflow inputs, upstream agent outputs, and saved drafts must be treated as untrusted data rather than instructions.

## Local runtime state

When the lifecycle runtime is supplied separately, its workflow drafts, permits, results, and hook state are stored under `PLUGIN_DATA`. Workflow Governor restricts runtime directories to mode `0700` and its database and draft files to mode `0600`. Run that lifecycle runtime under a dedicated operating-system identity when multiple untrusted users share a host.

The asynchronous `codex-workflows` runner also stores input snapshots, rendered prompts, raw Codex events, stderr, attempts, and final outputs under `PLUGIN_DATA` (or the documented per-user fallback) with private permissions. Do not include API keys, credentials, or unnecessary personal data. Private file modes are not a multi-tenant security boundary; use a dedicated operating-system identity when local users do not trust one another.

`workspace-write` and `danger-full-access` workflows require explicit run flags. Writer serialization prevents concurrent writer processes for one project but is not transactional isolation, rollback, or a substitute for separate worktrees and operating-system sandboxing.

## Reporting a vulnerability

Please report security issues through [GitHub private vulnerability reporting](https://github.com/BearsCLOUD/codex-workflow-governor/security/advisories/new), not a public issue. Include the affected version, reproduction steps, and impact.
