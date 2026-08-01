---
name: workflow-create
description: Explicitly create a new Workflow Governor draft for one repository and workflow ID without modifying the repository.
---

# Create Workflow Draft

Use this skill only after the user explicitly invokes `$workflow-create`.

1. Require one repository path and one new lower-case hyphenated workflow ID.
2. Read the nearest applicable `AGENTS.md` files and only the contracts they link for the requested scope.
3. Convert every one-line instruction rule into an `instruction_rules` entry with an exact line range and SHA-256 digest.
4. Bind every rule to a declared node or role, or record one specific `not_applicable` reason.
5. Define typed task templates, declared roles, bounded fan-out, nodes, deterministic guards, and a delegation policy with depth at most `2` and no arbitrary cycles.
6. Use only `parent` or `isolated` context mode.
7. Write every proposed role TOML and workflow field in English.
8. Compute role digests from the exact proposed TOML bytes.
9. Call the maintainer `workflow_draft` tool with revision `1`, the complete source object, and proposed role files.
10. Stop after reporting the draft digest and state that the repository is unchanged.

Never call `workflow_apply` from this skill. Never run Git commands or add Git policy.

