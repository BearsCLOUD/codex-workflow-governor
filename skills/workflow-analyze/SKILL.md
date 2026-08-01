---
name: workflow-analyze
description: Explicitly analyze one Workflow Governor workflow while keeping deterministic failures separate from advisory recommendations.
---

# Analyze Workflow

Use this skill only after the user explicitly invokes `$workflow-analyze`.

1. Require one repository path and one explicit workflow ID.
2. Call the reader `workflow_analyze` tool.
3. Present `deterministic_errors` as blocking facts.
4. Present `advisory_recommendations` as optional improvements.
5. State whether the workflow currently verifies.

Remain read-only. Never turn advice into an edit or claim advisory findings are enforcement failures. Never run Git commands.

