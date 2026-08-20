---
name: graph-completion
description: Complete decision-relevant knowledge graph gaps with source-linked candidate facts, validation, conflict handling, and explicit stop rules. Use for entities, attributes, and relations.
---

# Graph Completion

## Purpose

Fill missing entities, attributes, and relations that block a target query or decision.

Treat model predictions and graph similarity as candidate signals rather than accepted facts.

## Use

Use this skill when missing links break dependency, provenance, ownership, impact, identity, or evidence paths.

Use this skill when many records require consistent enrichment or when aliases, duplicates, stale facts, and conflicts require reconciliation.

Do not build a graph when a table or narrative answers the question more simply.

Do not design a universal ontology before the target query requires it.

## Inputs

Define the target query, graph scope, authoritative source classes, minimal entity types, minimal predicates, time semantics, confidence policy, acceptance rules, and stop criteria.

## Fact model

Retain subject, predicate, object, value types, status, source reference, extraction location, observed or valid time, confidence reason, and alias or replacement reference.

Use only `candidate`, `accepted`, `rejected`, `conflicted`, and `unresolved` as working fact statuses.

Never overwrite a conflicting fact silently.

## Gap map

Detect missing nodes, attributes, relations, evidence paths, unsupported relations, dangling entities, duplicate identities, stale facts, conflicts, and schema violations.

Prioritize only gaps that can change the target query, plan, or risk assessment.

## Workflow

1. Express the path, comparison, dependency, or decision that the graph must support.
2. Reuse the existing ontology and graph store when available.
3. Define only the missing types, predicates, cardinality, and time rules required by the target query.
4. Load current facts with provenance and preserve uncertain identities.
5. Record each gap with impact, required evidence, method, status, and owner.
6. Rank gaps by target impact, risk if wrong, and value of information.
7. Create a method card with source class, retrieval method, entity-resolution rule, validator, evidence format, and stop condition.
8. Generate source-linked candidates without mutating accepted truth.
9. Validate provenance, source independence, identity, types, relation direction, cardinality, time validity, and neighborhood consistency.
10. Run an independent critique and accept, revise, reject, or leave unresolved each candidate.
11. Commit accepted facts, retain conflict history, rerun diagnostics, and rerun the target query.
12. Repeat only for an open high-impact gap that can change the target result.

## Enrich, critique, correct

Use `Enrich -> Critique -> Correct` after every candidate wave.

During `Enrich`, add candidates with provenance and confidence.

During `Critique`, test evidence, identity, schema, time, conflicts, and true source independence.

During `Correct`, repair the fact, identity mapping, ontology rule, or acquisition method.

Never accept a graph-consistent relation without factual evidence.

Never raise confidence merely because several copied sources agree.

## Stop

Stop when high-impact target paths are complete or explicitly unresolved, accepted facts satisfy the graph contract, conflicts remain visible, and another wave is unlikely to change the answer.

Report unresolved gaps instead of filling them with plausible guesses.

## Output

Return accepted facts, rejected and conflicted candidates, the prioritized gap map, method cards, diagnostics, the target-query result, wave changes, and stop reason.
