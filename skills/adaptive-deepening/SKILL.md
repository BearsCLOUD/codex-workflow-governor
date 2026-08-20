---
name: adaptive-deepening
description: Deepen research or synthesis through evidence-driven waves chosen from the current gap map. Use when an upfront plan cannot reveal every important question.
---

# Adaptive Deepening

## Purpose

Produce an early useful synthesis, expose decision-relevant gaps, and deepen only gaps that can change the result.

Treat this method as progressive enrichment rather than iterative-deepening search.

## Use

Use this skill when evidence is distributed, the first synthesis exposes new questions, or independent gaps can be researched in parallel.

Do not use this skill for bounded lookup, deterministic transformation, or work where another wave cannot change the answer.

## Inputs

Record the objective, consumer, required output, source constraints, quality threshold, work budget, and costly-to-miss claims.

## Gap map

Maintain one compact table with `id`, affected claim, decision impact, required evidence, method, owner, status, confidence, and evidence references.

Use only `open`, `in_progress`, `resolved`, and `unresolved` as gap statuses.

Keep a Markdown table unless the task already owns another suitable state store.

## Workflow

1. Create a coarse question map without predicting every branch.
2. Produce the earliest useful synthesis from current evidence.
3. Mark assumptions, weak claims, conflicts, missing alternatives, and temporal uncertainty.
4. Rank gaps by decision impact and value of information.
5. Create one method card per selected gap.
6. Specify the question, source class, extraction format, validation rule, and stop condition in each method card.
7. Assign disjoint gaps to workers when parallelism reduces work without overlapping ownership.
8. Require workers to return evidence packets rather than acceptance decisions.
9. Run an independent critique against evidence, counterevidence, source independence, time validity, and method fitness.
10. Correct the evidence, reasoning, or method and keep unresolved gaps explicit.
11. Re-synthesize the result and update the gap map.
12. Repeat only when one open high-impact gap justifies another wave.

## Enrich, critique, correct

Use `Enrich -> Critique -> Correct` after every wave.

During `Enrich`, collect candidate evidence with provenance and confidence without silently replacing prior state.

During `Critique`, report concrete discrepancies without inheriting unsupported worker conclusions.

During `Correct`, accept, revise, reject, or leave unresolved each discrepancy using evidence.

Never let the critic invent replacement facts.

## Stop

Stop when high-impact gaps are resolved or explicitly unresolved, load-bearing claims have adequate evidence, material conflicts are reconciled, and another wave is unlikely to change the conclusion.

Stop at the declared budget and report the resulting limitation.

Treat more agents, sources, sections, or waves without decision impact as failure.

## Output

Return the synthesis, resolved and unresolved gap map, material conflicts, confidence limits, wave changes, and stop reason.
