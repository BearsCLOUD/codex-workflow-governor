---
name: workflow-check
description: Explicitly perform deterministic validation of one repository workflow source, lock, digests, and generated views.
---

# Check Workflow

Use this skill only after the user explicitly invokes `$workflow-check`.

1. Require one repository path and one explicit workflow ID.
2. Call the reader `workflow_check` tool.
3. Report deterministic errors exactly and separately from any explanation.
4. Report success only when `ok` is `true`.

Remain read-only. Never repair, render, apply, or run the workflow. Never run Git commands.

