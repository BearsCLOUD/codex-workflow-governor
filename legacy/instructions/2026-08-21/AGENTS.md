# Repository Instructions

## Scope and Simplicity

- Use the smallest implementation that directly achieves the requested outcome. Do not add abstractions, frameworks, governance layers, recovery machinery, or generalized infrastructure unless the requested outcome requires them or the user explicitly asks for them.
- Do not perform work outside the stated goal. Every action must have a direct and necessary causal path to the originally intended result; omit opportunistic cleanup, refactoring, audits, documentation, and adjacent improvements that do not directly enable that result.

## Delivery

- After completing and validating a requested repository change, commit the complete scoped change and push it to the authoritative upstream branch in the same task. Do not leave finished work only in the working tree unless the user explicitly says not to commit or push, or the remote blocks publication.
- Recheck the remote branch before publication, preserve unrelated worktree changes, and use a non-force push.
- Report the branch, commit SHA, push result, validation evidence, and any remaining blocker.

## Notion Synchronization

- For repository work linked to a Notion Design or delivery Task, re-fetch the live Notion records and authoritative remote branch before implementation. Treat Notion product authority and exact Git source evidence as separate layers.
- Keep an Approved Design's `Base SHA` immutable: it records the exact source reviewed for that revision and must not follow a moving branch automatically. Record the current remote `main` SHA, plugin version, and reconciliation time separately in the repository authority and active delivery Task.
- Before moving a delivery Task from `Ready` to `In progress`, compare the Approved Design baseline with the current remote head. Inspect the semantic delta, not only changed path names or commit counts.
- If the delta preserves the approved public contract, update the repository live snapshot and only the active delivery Task's source baseline and evidence; leave Product and the Approved Design unchanged. If it changes public CLI behavior, schemas, templates, plugin capabilities, safety or failure semantics, acceptance, or public documentation, keep execution gated, advance the Design to its next revision on the current head, and complete an independent delta review before implementation.
- After a validated non-force push, update the repository live snapshot and delivery Task to the exact pushed remote SHA. Do not rewrite the Approved Design baseline. Update Feature Map only after validation at that exact delivered SHA, and update Research only from real external-user evidence.
- Update Product, Functional Wiki, and Decisions only for semantic product-contract changes, not for every commit. Reuse the active delivery Task for source drift; do not create a separate reconciliation Task per commit.
- Do not mark the delivery Task `Done` when required Notion reconciliation, exact-SHA evidence, or publication verification is unavailable. Report the remaining synchronization blocker explicitly.
