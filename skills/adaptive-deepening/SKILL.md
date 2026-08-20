---
name: adaptive-deepening
description: Deepen research, decision support, or synthesis through evidence-driven waves selected from the current gap map. Use when an upfront plan cannot expose every important question.
---

# Adaptive Deepening

## Purpose

Build a useful first synthesis early, then deepen only the parts whose missing evidence or reasoning can change the result. The current draft is an observation: it exposes gaps that were invisible during initial planning.

This is evidence-driven deepening, not iterative-deepening depth-first search. It is a reusable method, not a workflow phase.

## Use when

- the question is open-ended or evidence is distributed across many sources;
- drafting reveals missing assumptions, weak claims, conflicts, or new branches;
- the result supports a decision and the value of more research varies by gap;
- independent gaps can be researched in parallel.

Do not use it for a bounded lookup, a fully specified transformation, or a task where another wave cannot materially change the answer.

## Minimum input

Record before starting:

- objective and intended consumer;
- required output and decision it must support;
- source and tool constraints;
- quality threshold, budget, and hard deadline;
- claims that would be costly if wrong.

## Working state

Maintain one compact gap map. Each gap has:

- `id` and affected claim or section;
- why it matters and what decision it may change;
- evidence or reasoning still needed;
- proposed method, source class, and owner;
- status: `open`, `in_progress`, `resolved`, or `unresolved`;
- confidence and evidence refs.

Do not create a complex state system for a small task. A Markdown table is enough.

## Optional role split

For multi-agent work, keep responsibilities explicit:

- the method planner converts selected gaps into method cards and acceptance rules;
- workers execute those cards and return evidence packets, not acceptance decisions;
- the critic receives the objective, evidence, and rules, but does not inherit an unsupported worker conclusion as a premise;
- the owner corrects the result, updates the gap map, and decides whether to repeat.

One agent may perform several roles for a small task, but critique remains a separate pass from enrichment.

## Workflow

1. **Initialize sparsely.** Create a coarse question map or outline. Do not try to predict every branch before reading evidence.
2. **Produce the first synthesis.** Write the earliest useful answer from current evidence. Mark assumptions and unsupported claims instead of hiding them.
3. **Diagnose gaps.** Inspect the whole result for missing evidence, shallow reasoning, contradictions, omitted alternatives, temporal uncertainty, and claims that do not support the requested decision.
4. **Prioritize.** Rank gaps by expected decision impact and value of information, not by section length or aesthetic completeness.
5. **Design the next wave.** For every selected gap, specify the search question, source class, extraction format, validation rule, and stop condition. The method is part of the work product.
6. **Enrich.** Research independent gaps in parallel when that reduces work without creating overlapping ownership. Return evidence packets, not free-form summaries.
7. **Critique.** Use a separate pass to test source independence, claim-evidence fit, contradictions, missing counterevidence, temporal validity, and whether the chosen method could answer the gap.
8. **Correct.** Repair the data, reasoning, or method. Never fix a failed critique only by making prose sound more certain. Keep unresolved items explicit.
9. **Re-synthesize.** Update the result and gap map. Add a new gap only when it can materially affect the objective.
10. **Stop or repeat.** Start another wave only when its expected value exceeds its cost and a specific open gap justifies it.

## Enrich -> Critique -> Correct

Use this loop after every evidence wave:

- **Enrich:** collect candidate evidence with provenance and confidence without silently replacing prior state.
- **Critique:** inspect candidates independently from the worker that produced them and report concrete discrepancies, not general impressions.
- **Correct:** accept, revise, reject, or leave unresolved each discrepancy, then re-score the affected gaps.

The critic does not invent replacement facts. Correction must point to evidence or explicitly record that evidence is unavailable.

## Stop rule

Stop when all high-impact gaps are resolved or explicitly unresolved, every load-bearing claim has adequate evidence, material conflicts are reconciled, and another wave is unlikely to change the conclusion. Also stop at the declared budget or deadline and report the resulting limitation.

More sections, sources, waves, or agents are not evidence of greater depth. Expansion without decision impact is a failure mode.

## Output

Return:

1. the current synthesis or recommendation;
2. the resolved and unresolved gap map;
3. material conflicts and confidence limits;
4. the wave log: what changed and why the loop stopped.

## Quality checks

- Every load-bearing claim has a traceable source or is labeled inference.
- High-impact alternatives and counterevidence were tested.
- Sources counted as independent are actually independent.
- Corrections changed the underlying evidence, reasoning, or method.
- Repeated waves show decreasing high-impact uncertainty.
- The final answer is no longer than required by the consumer.

## Workflow Governor integration

Use this method directly for a one-off task. Create a Governor workflow only when the method is recurring, large, or needs deterministic role and dependency control.

A reusable workflow normally contains these responsibilities:

`initial synthesis -> gap detection -> method planning -> worker fan-out -> independent critique -> correction -> re-synthesis -> owner stop gate`

Use the explicit `workflow-create` or `workflow-update` skills to encode that graph, `workflow-check` and `workflow-analyze` to validate it, `workflow-apply` to materialize it, and `workflow-run` to execute it.

The Governor owns the execution graph, immutable lock, permits, and run ledger. The task owns the evidence, gap map, method cards, and synthesis. Do not store research truth inside the execution graph.

Workers return bounded evidence packets. The workflow owner remains responsible for acceptance decisions, corrections, and the next-wave gate.

## Examples

- Research: draft a market landscape, detect an unsupported competitor claim, run a targeted primary-source wave, critique source independence, and revise.
- Decision support: compare deployment options, deepen only cost and recovery assumptions that can change the recommendation, then stop at decision parity.
- Synthesis: summarize a codebase, detect an unexplained runtime path, trace that path, correct the architecture narrative, and leave unrelated modules shallow.

## Method basis

The core pattern adapts the interleaving of evidence-based drafting and reasoning-driven deepening described in AgentCPM-Report:
https://arxiv.org/abs/2602.06540
