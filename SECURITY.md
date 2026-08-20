# Security

## Local runtime state

The `codex-workflows` runner stores input snapshots, rendered prompts, raw Codex events, stderr, attempts, and final outputs under `PLUGIN_DATA` (or the documented per-user fallback) with private permissions. Repository files, workflow inputs, and upstream model outputs are untrusted data rather than instructions. Do not include API keys, credentials, or unnecessary personal data. Private file modes are not a multi-tenant security boundary; use a dedicated operating-system identity when local users do not trust one another.

`workspace-write` and `danger-full-access` workflows require explicit run flags. Writer serialization prevents concurrent writer processes for one project but is not transactional isolation, rollback, or a substitute for separate worktrees and operating-system sandboxing.

## Reporting a vulnerability

Please report security issues through [GitHub private vulnerability reporting](https://github.com/BearsCLOUD/codex-workflow-governor/security/advisories/new), not a public issue. Include the affected version, reproduction steps, and impact.
