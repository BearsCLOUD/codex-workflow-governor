---
name: graph-completion
description: Complete decision-relevant knowledge graph gaps with source-linked candidate facts, validation, conflict handling, and explicit stopping rules. Use for research, planning, and synthesis over entities and relations.
---

# Graph Completion

## Purpose

Find and fill missing entities, attributes, and relations that prevent a graph from answering a concrete question or supporting a decision. Treat model predictions as candidate-generation signals, never as facts without evidence.

This is an evidence-grounded Knowledge Graph Completion method. It is reusable methodology, not a workflow phase.

## Use when

- important information is naturally expressed as entities and relations;
- missing links break dependency, provenance, ownership, impact, or evidence paths;
- many records must be enriched consistently;
- contradictions, duplicates, aliases, or stale facts must be reconciled.

Do not build a graph for a one-off answer that is simpler as a table or narrative. Do not design a universal ontology before the decision requires it.

## Minimum input

Define:

- the question or decision the graph must answer;
- graph scope and authoritative source classes;
- entity types and the smallest sufficient relation vocabulary;
- required provenance, time semantics, and confidence policy;
- acceptance, rejection, and stop criteria.

## Minimal record model

Each accepted or candidate fact should retain:

- stable subject, predicate, and object refs;
- entity and value types;
- status: `candidate`, `accepted`, `rejected`, `conflicted`, or `unresolved`;
- source refs and extraction location;
- observed or valid time when relevant;
- confidence and the reason for it;
- replacement or alias refs when identity changes.

Never silently overwrite a conflicting fact. Preserve both candidates and the reason one was accepted, rejected, or left unresolved.

## Gap classes

Detect at least these decision-relevant gaps:

- missing node, attribute, or relation required by the target query;
- broken path between a claim and its evidence;
- dangling or orphan entity;
- unsupported relation;
- conflicting values or relation targets;
- duplicate or unresolved entity identity;
- stale fact whose time validity is unknown;
- schema or type violation.

A low global completion percentage is not automatically a problem. Prioritize only gaps that can change the target answer, plan, or risk assessment.

## Workflow

1. **Define the target query.** Express what path, comparison, dependency, or decision the completed graph must support.
2. **Set the graph contract.** Reuse the existing ontology when one exists. Otherwise define only the entity types, predicates, cardinality, and time rules required for this task.
3. **Seed and normalize.** Load current facts with provenance. Resolve obvious aliases without merging uncertain identities.
4. **Detect gaps.** Run structural checks and inspect missing decision paths. Record each gap with impact, required evidence, and status.
5. **Prioritize.** Rank gaps by target-query impact, risk if wrong, and expected value of information.
6. **Design a method card.** For each selected gap state the source class, retrieval or extraction method, entity-resolution rule, validator, evidence format, and stop condition.
7. **Generate candidates.** Search and extract independent gaps in parallel when ownership is disjoint. Candidate generation may use graph neighborhoods or language models, but every candidate remains unaccepted.
8. **Validate candidates.** Check provenance, source independence, entity type, relation validity, cardinality, temporal validity, and consistency with the surrounding graph.
9. **Critique and correct.** Independently surface discrepancies, then accept, revise, reject, or leave unresolved each candidate. Correction may change the candidate or its retrieval method; it must not merely raise confidence.
10. **Commit and re-check.** Add accepted facts, retain conflict history, rerun diagnostics and the target query, then update the gap map.
11. **Stop or repeat.** Start another wave only for an open high-impact gap whose completion could change the target result.

## Enrich -> Critique -> Correct

Use this loop after every candidate wave:

- **Enrich:** add source-linked candidates without mutating accepted truth.
- **Critique:** test evidence, identity, schema, time, neighborhood consistency, and source consensus. Consensus improves confidence only when sources are genuinely independent.
- **Correct:** resolve the discrepancy in the fact, identity mapping, ontology, or acquisition method. Keep unresolved conflicts visible.

The critic must not infer that a graph-consistent edge is true. Structural fit is necessary but not sufficient; factual acceptance still requires evidence.

## Stop rule

Stop when all high-impact target paths are complete or explicitly unresolved, accepted facts satisfy the graph contract, material conflicts are recorded, and another wave is unlikely to change the answer. Report unresolved gaps rather than filling them with plausible guesses.

## Output

Return or update:

1. accepted entities and relations with provenance;
2. candidate, rejected, conflicted, and unresolved facts;
3. the prioritized gap map and method cards;
4. diagnostics and the target-query result;
5. a wave log explaining changes and the stop decision.

Use the graph store already owned by the task. Create a small JSON or Markdown representation only when no graph system exists and a durable graph is actually needed.

## Quality checks

- Stable entity identity is separated from labels and aliases.
- Every accepted fact has traceable provenance.
- Relation direction, type, cardinality, and time semantics are valid.
- Duplicate entities and contradictory facts are explicit.
- Independent sources are not double-counted through copied content.
- Model-generated candidates are never promoted solely by confidence wording.
- Completion improves the target query, not only aggregate graph density.

## Workflow Governor integration

A knowledge graph and a Governor workflow graph are different systems:

- the knowledge graph stores domain entities, relations, candidates, provenance, and conflicts;
- the Governor graph stores execution roles, dependencies, gates, permits, and run state.

Never store domain facts as workflow nodes merely because both structures are graphs.

Use the task's existing graph store for facts. Create a Governor workflow only when completion is recurring, large, or needs deterministic multi-agent control. A reusable execution graph normally contains:

`target query -> gap detection -> method planning -> candidate worker fan-out -> validation -> independent critique -> owner commit -> diagnostics and target-query rerun -> stop gate`

Use `$codex-workflows` to compile a bounded one-off run from the target query or to create, validate, plan, and execute a reusable project workflow.

Workers return candidate packets and never promote facts. One owner writes accepted facts, preserves conflict history, and decides whether another wave is justified.

## Examples

- Research: connect an organization, its products, source claims, and evidence; fill only missing links required to compare competitors.
- Decision support: complete dependencies and ownership edges needed to estimate the impact of replacing a service.
- Synthesis: resolve aliases and provenance paths before producing a consolidated account of several documents.
- Integration discovery: test `restaurant -> uses_booking_system -> provider`; reject copied directory claims and accept only current primary evidence.

## Method basis

The method combines classical Knowledge Graph Completion with evidence-grounded LLM candidate generation and validation. Useful primary references include:

- KICGPT: https://arxiv.org/abs/2402.02389
- CogMG: https://arxiv.org/abs/2406.17231
