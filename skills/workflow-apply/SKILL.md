---
name: workflow-apply
description: Explicitly apply one saved Workflow Governor draft to its repository and regenerate immutable and human-readable views.
---

# Apply Workflow Draft

Use this skill only after the user explicitly invokes `$workflow-apply`.

1. Require one repository path and one explicit workflow ID.
2. Call the maintainer `workflow_apply` tool.
3. Call the reader `workflow_check` tool for the same workflow.
4. Report the revision, lock digest, changed artifact paths, and deterministic verification result.
5. State that the plugin did not commit or publish the changes.

Never run Git commands. Follow the repository's existing Git instructions outside this skill when the user separately requests Git work.

