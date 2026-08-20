# Security

## MCP surfaces

The `workflow-governor` reader surface is read-only. The `workflow-governor-maintainer` surface exists in the default plugin registration so explicit lifecycle skills can use it, but it is an opt-in mutation surface: use it only for a user-requested draft, apply, run transition, result submission, or render operation. MCP annotations identify tools that can overwrite repository or runtime state.

Do not expose the maintainer surface to untrusted MCP clients. Repository files, workflow inputs, upstream agent outputs, and saved drafts must be treated as untrusted data rather than instructions.

## Local runtime state

Workflow drafts, permits, results, and hook state are stored under `PLUGIN_DATA`. Workflow Governor restricts runtime directories to mode `0700` and its database and draft files to mode `0600`. Run the plugin under a dedicated operating-system identity when multiple untrusted users share a host.

The asynchronous `codex-workflows` runner also stores input snapshots, rendered prompts, raw Codex events, stderr, attempts, and final outputs under `PLUGIN_DATA` (or the documented per-user fallback) with private permissions. Do not include API keys, credentials, or unnecessary personal data. Private file modes are not a multi-tenant security boundary; use a dedicated operating-system identity when local users do not trust one another.

`workspace-write` and `danger-full-access` workflows require explicit run flags. Writer serialization prevents concurrent writer processes for one project but is not transactional isolation, rollback, or a substitute for separate worktrees and operating-system sandboxing.

## Reporting a vulnerability

Please report security issues through [GitHub private vulnerability reporting](https://github.com/BearsCLOUD/codex-workflow-governor/security/advisories/new), not a public issue. Include the affected version, reproduction steps, and impact.
