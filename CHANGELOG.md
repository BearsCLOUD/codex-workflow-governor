# Changelog

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
