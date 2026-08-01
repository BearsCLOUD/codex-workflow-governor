---
name: workflow-update
description: Explicitly prepare a new out-of-repository draft revision for one existing Workflow Governor workflow.
---

# Update Workflow Draft

Use this skill only after the user explicitly invokes `$workflow-update`.

1. Require one repository path and one explicit workflow ID.
2. Call the reader `workflow_get` tool and preserve the current workflow identity.
3. Re-read changed `AGENTS.md`, linked contracts, and role TOML files before editing the draft.
4. Increment the source revision by exactly one.
5. Recompute every affected instruction and role digest from exact bytes.
6. Preserve existing declared routes unless the requested change requires a new route.
7. Keep every role and workflow artifact in English.
8. Call the maintainer `workflow_draft` tool with the complete replacement source and role file map.
9. Stop after reporting the draft digest and state that the repository is unchanged.

Never call `workflow_apply` from this skill. Never run Git commands or create Git policy.

