---
name: workflow-run
description: Explicitly execute one selected Workflow Governor workflow ID through guarded or advisory lifecycle tools.
---

# Run Workflow

Use this skill only after the user explicitly invokes `$workflow-run`.

1. Require one repository path and one explicit workflow ID.
2. Call the reader `workflow_check` tool and stop on any deterministic error.
3. Copy the exact current session ID from the Workflow Governor `SessionStart` hook context.
4. Call the maintainer `workflow_start_run` tool with that session ID and the requested mode, then report any downgrade reason immediately.
5. Read the current node from run status.
6. For each structural node, collect the required evidence and call `workflow_advance`.
7. For each task or fan-out node, call `workflow_prepare_dispatch` with typed inputs, bounded depth, and repository-relative allowed paths.
8. Call the Codex Agent tool once with the returned `spawn_arguments` object exactly and without adding, removing, or rewriting arguments.
9. Require the subagent to call `workflow_submit_result` with its `workflow-result.v1` packet before stopping.
10. Call `workflow_status`, then call `workflow_advance` with the declared evidence and skips.
11. Continue until the run status is terminal.
12. Report graph compliance only when the mode is `guarded` and the final status is `completed`.

Treat `advisory` as unverified execution. Never claim enforcement when hooks were unavailable, untrusted, or bypassed. Never run Git commands or create Git policy.
