---
name: workflow-visualize
description: Explicitly inspect or regenerate deterministic Mermaid and WORKFLOW.md views for one Workflow Governor workflow.
---

# Visualize Workflow

Use this skill only after the user explicitly invokes `$workflow-visualize`.

1. Require one repository path and one explicit workflow ID.
2. Call the reader `workflow_get` tool and verify the lock digest.
3. Call the maintainer `workflow_render` tool only when the user asked to update generated files.
4. Otherwise present the existing deterministic Mermaid path and graph summary without repository mutation.
5. Report every generated path when rendering occurs.

Generate views only from the lock. Never infer missing graph edges or run Git commands.

