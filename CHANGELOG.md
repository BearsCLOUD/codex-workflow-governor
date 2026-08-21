# Changelog

## 0.10.0 — 2026-08-21

### Changed

- Standardized both bundled review workflows on `gpt-5.6-sol` with `medium` reasoning.
- Pinned every task in the other bundled workflows and the `workflow init` starter to `gpt-5.6-luna` with `high` reasoning instead of inheriting host defaults.

## 0.9.0 — 2026-08-21

### Added

- Added `builtin:workflow-audit` for read-only review of any qualified project, user, or built-in workflow using real target inputs.
- Added deterministic preflight evidence, six bounded audit lenses, skeptical finding challenge, and a strict risk-ranked audit verdict through three pinned read-only roles.

### Security

- The audit workflow treats the target definition and all upstream results as untrusted data, never runs the target workflow, and gives every task a pinned read-only sandbox.

### Fixed

- Removed the obsolete skill-level `policy.products` override so the bundle passes current plugin validation.

## 0.8.0 — 2026-08-20

### Removed

- Removed the seven `workflow-*` native Agent/MCP lifecycle skills because they are not part of the `codex-workflows` template CLI and their required MCP and hook surfaces are not registered by this plugin.

### Changed

- Focused plugin metadata, documentation, and methodology integration on asynchronous, adaptive, and persistent `codex exec` workflows.

## 0.7.0 — 2026-08-20

### Added

- Added `prompt-plan` and `prompt-run` to select `direct`, `adaptive-deepening`, `graph-completion`, or a bounded hybrid from one prompt through a strict read-only `codex exec` selection pass.
- Added generated validated wave DAGs with bounded evidence fan-out, separate validation and critique contexts, owner-only fact acceptance, deterministic next-wave gates, total-call/deadline stops, and resumable detached execution.
- Added `prompt-status`, `prompt-result`, `prompt-resume`, and review-gated `prompt-save-template`, plus durable prompt, selection, methodology snapshots, wave definitions, evidence artifacts, gap/graph state, hash-chained events, and Markdown/JSON results.

### Security

- Prompt compilation is read-only and rejects requested write sandboxes because prompts and generated plans cannot self-issue Governor mutation permits.
- Installed methodology paths, plugin version, snapshots, and SHA-256 digests are pinned and revalidated before resumed execution; repository content and upstream outputs remain untrusted task data.

## 0.6.0 — 2026-08-20

### Added

- Added explicit `until-cancelled` workflows whose supervisor runs bounded acyclic DAG cycles without retaining an interactive caller.
- Added durable hash-chained `state.jsonl`, generated `STATE.md`, atomic cursor checkpoints, cross-cycle fan-out idempotency keys, restart recovery, deterministic jitter, exponential backoff, and a consecutive-failure circuit breaker.
- Added race-safe `tail`, `pause`, `resume`, and graceful `cancel` lifecycle commands plus single-instance enforcement for each project/workflow/instance key.
- Added read-only `loop-monitor` and `github-issue-worker` built-ins with external mutations denied by default.

### Security

- Persistent write-capable tasks now require explicit `git-worktree` isolation as well as the existing sandbox opt-in; loop permissions are fixed in workflow configuration and injected into every cycle's developer instructions.
- Lifecycle projections redact recognizable credentials, validate ordered event digests, and detect corrupt or truncated JSONL tails.

## 0.5.1 — 2026-08-20

### Fixed

- Reconcile terminal `codex exec` turns after a bounded grace period instead of waiting for the outer task timeout when output is missing or invalid.
- Persist distinct missing, malformed, and schema-invalid output reasons, attempt activity timestamps, retry decisions, and process-group cleanup evidence in run status and events.
- Resume interrupted `running` supervisors from durable attempt state without duplicating retries, while terminating recorded orphan process groups safely.

## 0.5.0 — 2026-08-20

### Added

- Added `codex-exec-workflow.v2` with byte-exact project-agent pins, workflow snapshots, drift detection, and resolved execution metadata while retaining full v1 support.
- Added `agent list|show|validate|schema|register|create|update|repin`, strict external/generated authoring specs, dry runs, transactional multi-workflow repinning, and concurrent-update detection.
- Added `workflow bind-agent` and conflict-safe `workflow install` support for bundled project roles.

### Changed

- Migrated `adversarial-plugin-review` to three pinned `gpt-5.6-sol`, `xhigh`, read-only project roles; all six review lenses use `adversarial-reviewer`.
- Agent-bound workers now receive pinned model, reasoning, developer instructions, and sandbox values through explicit `codex exec` arguments.
- Kept `fanout-synthesize` on v1 and kept lifecycle MCP and hook registrations outside the bundled plugin.

## 0.4.0 — 2026-08-20

### Added

- Added the built-in `adversarial-plugin-review` workflow with independent review lenses, a skeptical finding-challenge pass, strict evidence schemas, and a final release verdict.

### Changed

- Removed the bundled `workflow-governor` and `workflow-governor-maintainer` MCP registrations from the plugin.
- Removed the bundled lifecycle hook registration from the plugin.
- Lifecycle MCP tools, when needed, must now be supplied separately from the plugin.

## 0.3.0 — 2026-08-20

### Added

- Added the self-contained `codex-workflows` skill and CLI for reusable asynchronous `codex exec` DAGs.
- Added bounded fan-out, detached runs, cancellation, retries, strict JSON Schema outputs, dependency binding, and persisted run artifacts.
- Added project, user, and built-in workflow scopes plus the `fanout-synthesize` template.
- Added async runner and public-release security regression tests.
- Added a security policy and private vulnerability reporting guidance.
- Added conservative call budgets, per-task fan-out caps, project-wide writer serialization, and explicit write-sandbox opt-ins.

### Security

- Contained workflow IDs and generated paths inside validated Git worktrees.
- Revalidated guarded-run lock identity, revision, and digest at stop time.
- Restricted ledger, draft, and run-state permissions and rejected unsafe database symlinks.
- Corrected MCP mutation annotations and made the documentation archive reject recognizable secrets.
- Added workflow-snapshot integrity checks, process-group cancellation, strict local schema-keyword parity, finite-number checks, and symlink-safe generated writes.

### Changed

- Aligned package, MCP server, plugin, and changelog versions.
- Documented the boundary between governed native-Agent workflows and asynchronous codex-exec workflows.

## 0.2.0 — 2026-08-20

### Added

- Added `score-documentation-quality` under `skills/`.
- Added adaptive quick-edit, formal 100-point scoring, and deep migration modes.
- Added static scanning, deterministic scoring, instruction-graph validation, and byte-exact archive utilities.
- Added the skill icon, interface metadata, schemas, calibration examples, and 25 regression tests.

### Changed

- Updated plugin metadata and documentation to expose documentation governance alongside workflow governance.

## 0.1.0 — 2026-08-01

- Initial Codex Workflow Governor plugin.
