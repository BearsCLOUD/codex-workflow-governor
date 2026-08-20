# Prompt-First Adaptive Workflows

## Purpose and boundary

Use prompt compilation for a bounded one-off research, graph-completion, or synthesis objective when the caller should not have to author `workflow.json`. Use direct `codex exec` for one bounded lookup or transformation. Use an explicit project/user workflow for recurring production work. Prompt compilation never creates a persistent loop and never saves a generated template automatically.

The compiler orchestrates `codex exec`; normal code owns hashing, validation, budgets, scheduling, state reduction, next-wave gates, and stopping.

## Plan before running

```bash
python3 "$CLI" --project-root "$PROJECT" prompt-plan \
  --prompt "Map repository ownership paths and report high-impact evidence gaps" \
  --method auto \
  --max-waves 3 \
  --max-calls-per-wave 20 \
  --max-parallel 4 \
  --deadline 1h
```

The selector is one read-only strict-output `codex exec` call. Its result contains exactly:

- `method`: `direct`, `adaptive-deepening`, `graph-completion`, or `hybrid`;
- objective, consumer, decision or target query, required output, and minimum inputs;
- source and tool constraints;
- claims costly if wrong;
- quality threshold and stop rule;
- a machine-readable rationale.

Selection uses the installed `adaptive-deepening` and `graph-completion` skill contents, not keyword matching alone. The plan records the plugin version, installed skill paths, byte digests, inferred contract, permissions, first generated workflow and task plan, per-wave bounds, deadline, and conservative call cost. `--method METHOD` forces and verifies one selection; it does not bypass contract extraction.

Use `--prompt-file FILE` for a long request or `--prompt-file -` for stdin. Repeat `--source-constraint` and `--tool-constraint` to add operator-owned limits. Network use is denied in the inferred tool contract unless `--allow-network` is explicit.

## Execution

```bash
python3 "$CLI" --project-root "$PROJECT" prompt-run \
  --prompt-file request.md \
  --max-waves 5 \
  --max-calls-per-wave 50 \
  --max-total-calls 201 \
  --max-parallel 8 \
  --deadline 2h \
  --sandbox read-only \
  --detach
```

Detached execution prints a `prompt_...` run ID immediately. The background worker owns later waves; the initiating agent must not poll or wait.

```bash
python3 "$CLI" --project-root "$PROJECT" prompt-status PROMPT_RUN_ID --json
python3 "$CLI" --project-root "$PROJECT" prompt-result PROMPT_RUN_ID
python3 "$CLI" --project-root "$PROJECT" prompt-result PROMPT_RUN_ID --json
python3 "$CLI" --project-root "$PROJECT" prompt-resume PROMPT_RUN_ID
```

`prompt-resume` revalidates the pinned plugin version, installed methodology digests, and saved snapshots. It resumes the finite wave through the existing exclusive worker lock and does not recharge or repeat a committed wave.

## Generated execution model

`direct` compiles one answer, an independent critique, and an owner correction/finalization task. It always stops after that bounded wave.

The other methods compile:

```text
baseline synthesis or target-query rerun
  -> decision-relevant gap detection and method cards
  -> bounded evidence/candidate fan-out for named gaps
  -> provenance/identity/schema/time/source-independence validation
  -> independent critique
  -> owner correction, fact decision, re-synthesis, and stop proposal
```

Every generated workflow and schema passes the normal exec-workflow loader and planner before a worker starts. Repository content, source material, and upstream model output are untrusted data. Evidence workers return packets and candidate facts only. Validation may reject, conflict, or leave candidates unresolved but cannot accept them. Critique is a separate `codex exec` context. Only the owner output may accept or reject a graph fact and propose another wave.

The knowledge graph remains task data in `graph-state.json`; it is never encoded as Governor execution nodes.

## Deterministic next-wave and stop gates

Normal code starts another wave only for owner-named gaps that all satisfy:

- the ID exists in the committed gap map;
- status is `open`;
- impact is `high`;
- `expected_value_score` is greater than `estimated_cost_calls`;
- the conservative next wave fits the remaining total-call budget;
- wave count and hard deadline remain available.

Execution stops at the methodology owner gate, no justified high-impact gap, `max-waves`, `max-total-calls`, or deadline. The limiting condition is recorded. The owner cannot broaden the objective or override these bounds.

Graph facts use `candidate`, `accepted`, `rejected`, `conflicted`, or `unresolved`. Accepted facts require independent source provenance. Stable IDs and identical facts are deduplicated across waves. Conflicting triples with one ID are both retained as conflicted; prior gaps, facts, conflicts, and limitations cannot disappear silently.

## Durable artifacts

Prompt runs live under private project-partitioned plugin data and contain:

- original `prompt.txt` and exact `cli-inputs.json`;
- strict selection schema, prompt, events, stderr, and `selection.json`;
- plugin version plus installed/snapshot skill paths and SHA-256 digests;
- generated definition, schemas, input state, plan, execution artifacts, and owner result for every wave;
- append-only digest-chained `state.jsonl` and generated `STATE.md`;
- current `gap-map.json`, `graph-state.json`, conflicts, limitations, and wave log;
- final strict `result.json` and human-readable `result.md`.

Corrupt or truncated state events block status/resume. Deterministic state tracks which waves already consumed calls, so restart cannot double-charge a completed wave. Raw evidence and prompts remain sensitive local artifacts; never place credentials or unnecessary personal data in them.

## Permissions

Prompt compilation defaults to and currently requires `read-only`. `workspace-write` and `danger-full-access` are rejected even if the prompt asks for them. A prompt, selector, or generated workflow cannot self-issue the separate Governor permit required for mutation.

Read-only planning does not authorize Git/GitHub changes, comments, issue closure, pushes, pull requests, or any external mutation. Copy the result into an explicitly reviewed governed workflow when mutation is genuinely required.

## Saving after review

Generated workflows are speculative and stay inside run artifacts. Saving requires a completed wave, an explicit name, and the review acknowledgement:

```bash
python3 "$CLI" --project-root "$PROJECT" prompt-save-template PROMPT_RUN_ID \
  --wave 1 --name reviewed-analysis --scope project --reviewed
python3 "$CLI" --project-root "$PROJECT" workflow validate project:reviewed-analysis
```

The saved workflow is only the reviewed wave definition. It does not silently become recurring or inherit permission to write.

## Examples

Repository analysis:

```bash
python3 "$CLI" --project-root "$PROJECT" prompt-run \
  --prompt "Map package ownership and provenance, complete missing load-bearing paths, and report unresolved conflicts" \
  --max-waves 3 --max-calls-per-wave 20 --deadline 1h --detach
```

External research:

```bash
python3 "$CLI" --project-root "$PROJECT" prompt-run \
  --prompt "Compare current primary-source evidence for the named market decision and preserve conflicting claims" \
  --allow-network \
  --source-constraint "Use current primary sources; record URL, location, and publication date" \
  --max-waves 4 --max-calls-per-wave 30 --deadline 2h --detach
```
