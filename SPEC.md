# Local MCP Specification

## Product value

Codex users can plan, start, observe, and explicitly control local Workflow
Governor runs through typed tools without reconstructing shell commands or
parsing human-oriented CLI output. The existing workflow CLI and its
`codex exec` workers remain the only execution path.

## User and pain

The user is a local developer with an authenticated Codex CLI and one or more
Git worktrees. They already can use the shell CLI, but tool-using Codex sessions
otherwise need to assemble commands, temporary JSON files, and lifecycle
polling by hand.

## Why MCP

The model chooses one of four narrow actions and supplies structured inputs.
The MCP server provides the real local capability, validates project authority,
and returns bounded machine-readable summaries. MCP does not select workflow
methods, execute model work itself, or introduce another runtime.

## Surface

The plugin starts one local JSON-RPC-over-stdio server. There is no custom UI,
HTTP listener, OAuth flow, hosted state, account, telemetry, or background
upload.

- `workflow_plan`: validate inputs and return the deterministic bounded plan.
- `workflow_run`: start exactly one detached run under a caller-owned UUIDv4.
- `workflow_status`: read a bounded summary by run ID or mutation request ID.
- `workflow_control`: request pause, resume, or cancel under a caller-owned
  UUIDv4 and return accepted versus observed state.

`workflow_plan` and `workflow_status` are read-only tools. Run and control are
annotated as mutating/open-world actions and retain the CLI's explicit sandbox,
call-budget, and worktree gates.

## Architecture

```text
Codex MCP client
  -> plugin .mcp.json
  -> mcp/server.py (validation, authorization, bounded envelopes)
  -> scripts/codex_workflows.py (fixed package-relative launcher)
  -> skills/codex-workflows/scripts/codex_workflows.py
  -> detached supervisor
  -> codex exec workers
```

The MCP server never imports `workflow_governor` lifecycle modules. The public
skill CLI owns its small contract helpers directly and exposes internal MCP JSON
modes for qualified-only resolution, non-writing status, correlated mutations,
and lifecycle acknowledgement.

Mutation idempotency is owned by the CLI supervisor, not the MCP adapter. The
CLI keeps one private SQLite mutation ledger beside its existing per-project run
storage. Run directories, loop events, checkpoints, artifacts, and results keep
their existing file formats; SQLite is not a workflow-state backend.

## Project authorization

MCP tools cannot register projects. A separate local helper authorizes or
revokes canonical Git worktrees in a private registry under
`$CODEX_HOME/workflow-governor-mcp/`. Authorization records stable filesystem
and Git-directory identities and a credential-free origin digest. Every call
revalidates the identity before invoking the CLI.

## Protocol and data limits

- Maximum JSON-RPC frame and MCP result: 1 MiB.
- Maximum JSON depth: 32; object keys: 4096; array items: 10,000;
  string: 256 KiB.
- Canonical workflow inputs and private temporary input file: 1 MiB.
- CLI stdout: 2 MiB; stderr: 64 KiB; redacted error excerpt: 2 KiB.
- Plan/status/control timeout: 30 seconds; detached start acknowledgement:
  60 seconds.

All tool payloads use the closed `codex-workflow-mcp-result.v1` envelope and
omit prompts, inputs, model output bodies, credentials, usernames, and absolute
artifact paths.

## Mutation recovery

Run and control require a caller-generated UUIDv4. The CLI reserves each
mutation with `BEGIN IMMEDIATE` in one project-local SQLite ledger whose
`request_id` primary key is shared by run and control operations. The durable
row records the normalized argument digest, operation, reserved run ID, phase,
and worker identity. Identical retries resume the same row; changed reuse fails.

For a new run, the CLI commits the reserved run ID before preparing its run
directory. Persistent runs record their request-owned publishing marker and
publish the complete request-tagged snapshot under the same instance lock; a
different request cannot steal an interrupted publication. A CLI worker claims
the ledger row transactionally before executing;
concurrent or recovered spawns therefore allow only one live supervisor. Control
retries reconcile an already-applied desired state before acknowledgement.

The former `.mcp-requests`, `.mcp-run-requests`, and
`.mcp-control-requests` JSON entries are legacy read-only inputs. Status may
resolve one unambiguous entry, but mutations never rewrite, migrate, or delete
them. Status by request ID remains the reconciliation path after an ambiguous
timeout.

## Compatibility

Existing CLI commands, path references, workflows, and human `status --json`
remain supported. MCP accepts only qualified `project:`, `user:`, or `builtin:`
references and invokes a qualified-only CLI gate. No release version is assigned
by this specification.

## Acceptance

- MCP initialize, tools/list, tools/call, notifications, errors, EOF, limits,
  and annotations pass protocol tests.
- Root helper identity, permissions, linked-worktree, tamper, origin, revoke,
  and concurrency cases pass.
- Plan/run/status/control pass happy, unauthorized, malformed, timeout,
  redaction, and lifecycle cases.
- SQLite transaction, run-publication, control-application, worker-claim, and
  acknowledgement fault injection proves one mutation identity cannot create
  duplicate runs or supervisors.
- Concurrent identical calls converge, digest-mismatched or cross-operation
  reuse fails, and legacy JSON bytes remain unchanged under lookup and retry.
- Full-checkout subprocess tests prove the public MCP/CLI path does not import
  or call the legacy lifecycle backend.
- Plugin validation, package-relative clean installation, and fresh-session
  discovery pass.
