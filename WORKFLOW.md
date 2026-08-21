# Codex Workflow Governor Delivery

## Constitution

1. Let Notion own product and Design meaning and let this repository own development execution and technical evidence.
2. Require a delivery Task for product mutation unless the change is explicitly instruction-only and classified `Not product`.
3. Freeze Done criteria and required gates before Execution and return to the owning authority before changing them.
4. Keep each contract narrow and explicitly routed, and do not add a contract, gate, receipt, role, workflow node, or other governance unless executable behavior requires it.
5. Run repeatable execution through [codex-workflow-governor:codex-workflows](skills/codex-workflows/SKILL.md); one isolated stage may use its direct skill.
6. Give every writer an exclusive write set and keep shared authorities, schemas, integration, and publication with the primary agent.
7. Complete acceptance before source publication and keep publication, delivery, runtime identity, and functional acceptance separate.
8. Start mutation from a freshly verified `origin/main` in a clean isolated worktree; once the approved goal authorizes repository mutation, make the necessary edits, one atomic commit, and a non-force push without separate step-by-step permission.
9. Resolve development or execution conflicts by correcting repository authority and checks; resolve product or Design conflicts in Notion without inventing a repository decision.
10. Block only the dependent mutation, continue every independent ready lane, and keep each receipt state separate.

<!-- owner:workflow-delivery -->
## Scope and precedence

- Apply this workflow from an approved goal through source validation, publication, and delivery reconciliation.
- The constitution in [AGENTS.md](AGENTS.md) outranks this body, every linked skill, role, and workflow template.
- A development conflict is corrected in the repository before execution continues; a product or Design conflict pauses only its dependent mutation while independent engineering work continues.
- A conflict that neither authority can resolve pauses only the specific dependent action and is recorded as a blocker.

## Flow

1. Resolve the approved goal, authority, scope, dependencies, target branch, and applicable delivery Task.
2. Inspect behavior, sources, consumers, and reuse before adding a service, abstraction, schema, or contract.
3. Classify the change and satisfy the Design gate; instruction-only maintenance is `Not product`.
4. Freeze verifiable Done criteria and exclusive write sets before implementation.
5. Implement the smallest safe change and advance stages only on direct evidence.
6. Run independent acceptance, publish the accepted source, and reconcile the exact delivered revision.

## Instruction-only changes

- Treat edits limited to repository instructions, their inactive archive, and the README's instruction-chain route as `Not product` when they do not change CLI behavior, schemas, templates, workflow runtime, plugin capabilities, safety semantics, or functional acceptance.
- Preserve the approved public contract and do not add a runner, controller, queue, ledger, graph, manifest, or recovery mechanism for an instruction-only change.
- Keep source validation, commit, push, delivery, runtime identity, and functional acceptance as separate evidence even when some states are `not_performed`.

## Delegation and write sets

- Use a direct skill for one isolated stage; use [codex-workflow-governor:codex-workflows](skills/codex-workflows/SKILL.md) for multi-stage, detached, fan-out, or reusable graphs.
- Give each writer an exclusive write set. The primary agent owns shared authorities, schemas, integration, tests spanning write sets, commit, and publication.
- Serialize write-capable workers for one repository; read-only workers may run concurrently only within the validated workflow bounds.
- Read the skill's [workflow format](skills/codex-workflows/references/workflow-format.md), [prompt workflow](skills/codex-workflows/references/prompt-workflows.md), [loop workflow](skills/codex-workflows/references/loop-workflows.md), and [project agents](skills/codex-workflows/references/project-agents.md) references before changing those contracts.

## Stage and gate policy

- New or expanded user-visible behavior requires an `Approved` or `Implemented` Design; existing and `Not product` changes do not.
- A delivery Task is `Blocked` only when no authorized action can advance its Done criteria and a dependency prevents completion.
- Missing mandatory source validation blocks source; missing external, delivery, or runtime evidence is `not_performed`.
- Use deterministic substitutes only at approved test, development, or sandbox boundaries and never as production fallback or live evidence.

## Receipts

- Keep `source_validated`, `committed`, `pushed`, `delivered`, and `runtime_accepted` as separate receipt states.
- Record unavailable evidence as `not_performed`; do not infer delivery, reconciliation, runtime identity, or functional acceptance from a green test or a successful push alone.
- Acceptance precedes publication. Source publication never claims receiver acceptance, runtime reconciliation, delivery, or functional acceptance.

## Repository delivery contract

- The authoritative branch is `main`; recheck the current `origin/main` immediately before implementation and again immediately before publication.
- Preserve unrelated worktree changes, use one atomic commit for the complete scoped change, and publish with a non-force `git push origin main`.
- Verify the pushed remote SHA exactly, record the branch, commit SHA, push result, and validation evidence, and do not rewrite an Approved Design's immutable Base SHA.
- After a validated push, reconcile the repository live snapshot and active delivery Task to the exact pushed SHA. Update Feature Map only after validation at that exact delivered SHA; leave Product, Functional Wiki, Decisions, and Research unchanged unless their semantic authority or real external-user evidence changes.
- Do not mark a delivery Task `Done` while required Notion reconciliation, exact-SHA evidence, or publication verification is unavailable; report the blocker instead.

## Procedure routing

| Trigger | Route |
| --- | --- |
| Planning, Design, Execution, independent acceptance, or publication | [codex-workflow-governor:codex-workflows](skills/codex-workflows/SKILL.md) |
| One isolated stage | The stage-specific skill routed by its owning project definition |
| Instruction and documentation maintenance | [score-documentation-quality](skills/score-documentation-quality/SKILL.md) |

The repository's executable workflow definitions, schemas, tests, and skill references remain the detailed technical authorities. This document routes to them; it does not copy their implementation contract.
