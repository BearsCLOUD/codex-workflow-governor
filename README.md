# Codex Workflow Governor

Codex Workflow Governor provides reusable asynchronous `codex exec` workflows with bounded fan-out, explicit data dependencies, strict JSON outputs, detached execution, persisted artifacts, prompt-compiled adaptive waves, and explicitly configured recurring loops.

The implementation uses only the Python standard library. It does not require the OpenAI Agents SDK, LangGraph, CrewAI, Temporal, or a web service.

## Requirements

- Linux or macOS with Python 3.11 or newer;
- a current Codex CLI installation authenticated with `codex login`;
- a Git worktree when a workflow is allowed to modify a repository.

The async runner reuses the CLI login. It does not require a separate API key.

## Asynchronous Codex workflows

The self-contained CLI lives inside the `codex-workflows` skill:

```bash
CLI=skills/codex-workflows/scripts/codex_workflows.py
PROJECT=/absolute/project/root
python3 "$CLI" --project-root "$PROJECT" workflow list
python3 "$CLI" --project-root "$PROJECT" workflow validate builtin:fanout-synthesize
python3 "$CLI" --project-root "$PROJECT" plan builtin:fanout-synthesize --inputs /path/to/inputs.json
python3 "$CLI" --project-root "$PROJECT" run builtin:fanout-synthesize --inputs /path/to/inputs.json --detach
```

`run --detach` prints a run ID. Use it for monitoring and results:

```bash
python3 "$CLI" --project-root "$PROJECT" status RUN_ID --json
python3 "$CLI" --project-root "$PROJECT" wait RUN_ID --timeout 3600
python3 "$CLI" --project-root "$PROJECT" result RUN_ID
python3 "$CLI" --project-root "$PROJECT" cancel RUN_ID
```

An explicit `loop.mode=until-cancelled` turns the same validated acyclic DAG into bounded recurring cycles. It must run detached; the supervisor owns scheduling and recovery after the caller receives the run ID. Use the loop lifecycle commands instead of polling from an interactive agent:

```bash
python3 "$CLI" --project-root "$PROJECT" workflow validate builtin:github-issue-worker
python3 "$CLI" --project-root "$PROJECT" plan builtin:github-issue-worker \
  --inputs skills/codex-workflows/assets/workflows/github-issue-worker/example-inputs.json
python3 "$CLI" --project-root "$PROJECT" run builtin:github-issue-worker \
  --inputs skills/codex-workflows/assets/workflows/github-issue-worker/example-inputs.json --detach
python3 "$CLI" --project-root "$PROJECT" tail RUN_ID --follow
python3 "$CLI" --project-root "$PROJECT" pause RUN_ID
python3 "$CLI" --project-root "$PROJECT" resume RUN_ID
python3 "$CLI" --project-root "$PROJECT" cancel RUN_ID
```

Each loop cycle has its own call and wall-clock bounds. The supervisor commits the cursor only after the complete cycle succeeds, caches declared fan-out idempotency keys before advancing, applies deterministic jitter and bounded backoff, and opens a circuit after repeated failures. Only one active run may own the same project/workflow/instance key. `state.jsonl` is an ordered, hash-chained audit log; `STATE.md` is an atomic generated operator projection that `status` can rebuild. `loop-monitor` is the generic template. `github-issue-worker` discovers and triages issues read-only; it cannot comment, close, push, open or merge PRs, or delete branches unless a copied workflow is explicitly changed and revalidated.

Reusable workflow scopes are:

- project: `.codex/exec-workflows/<name>/`;
- user: `$CODEX_HOME/exec-workflows/<name>/`;
- built-in: `skills/codex-workflows/assets/workflows/<name>/`.

Each task has a strict root-object JSON Schema. The runner invokes `codex exec --ephemeral --json --output-schema ... --output-last-message ...`, independently validates the saved final JSON, and preserves prompts, JSONL events, stderr, attempts, task results, and run state outside the target repository. Read-only workers run concurrently up to `max_parallel`; write-capable workers require an explicit run flag and are serialized project-wide, including across runs.

The supervisor watches Codex JSONL events while the child is live. After `turn.completed`, `turn.failed`, or `turn.cancelled`, it allows a two-second output-flush grace period, then reconciles the terminal event, process group, exit state, and strict final output. Missing, malformed, and schema-invalid outputs receive distinct stable failure metadata; retries remain bounded by the task contract. A restarted worker resumes durable attempt state, cleans up recorded orphan process groups, and cannot consume the same retry twice. `CODEX_WORKFLOWS_TERMINAL_GRACE_SECONDS` may set a bounded `0.05`–`60` second grace for testing or host-specific flushing.

`plan` discloses the workflow digest, sandbox, working directory, model settings, timeouts, retries, fan-out bounds, and conservative call count. For persistent loops it reports a per-cycle cost model and correctly describes the total as unbounded until cancellation. The default budget is 5000 model calls; higher budgets and write-capable sandboxes require explicit command-line opt-ins. Persistent write tasks additionally require declared `git-worktree` isolation. Persisted inputs and artifacts may contain sensitive data, so do not pass credentials or unnecessary personal information.

The graph, readiness, dependency blocking, fan-out item order, and artifact layout are deterministic for fixed workflow files and inputs. Model text and semantics are not deterministic.

### Prompt-first adaptive runs

For a bounded one-off objective that needs method selection but not a hand-authored reusable workflow, `prompt-plan` runs a small strict read-only selection pass and compiles the first validated wave. It chooses `direct`, `adaptive-deepening`, `graph-completion`, or `hybrid` from the installed methodology contracts, records their exact paths and SHA-256 digests, and discloses inferred contracts, permissions, fan-out, retries, model settings, deadline, and conservative cost before execution.

Repository-analysis example:

```bash
python3 "$CLI" --project-root "$PROJECT" prompt-plan \
  --prompt "Map repository ownership and provenance paths, then report unresolved high-impact gaps" \
  --max-waves 3 --max-calls-per-wave 20 --max-parallel 4 --deadline 1h
python3 "$CLI" --project-root "$PROJECT" prompt-run \
  --prompt "Map repository ownership and provenance paths, then report unresolved high-impact gaps" \
  --max-waves 3 --max-calls-per-wave 20 --max-parallel 4 --deadline 1h --detach
```

External-research example, with network use disclosed explicitly:

```bash
python3 "$CLI" --project-root "$PROJECT" prompt-plan \
  --prompt-file market-question.md --allow-network \
  --source-constraint "Prefer current primary sources and record publication dates" \
  --max-waves 4 --max-calls-per-wave 30 --deadline 2h
```

Use `prompt-status RUN_ID`, `prompt-result RUN_ID`, and `prompt-resume RUN_ID`. Each generated wave is a normal strict exec-workflow DAG; evidence workers cannot accept facts, validation cannot promote candidates, critique is a separate `codex exec` context, and only the owner output may accept/reject facts or propose named next gaps. Normal code checks that a proposed next gap is open, high-impact, and worth more than its declared call cost, then stops at the method gate, wave cap, total-call budget, or deadline.

Prompt compilation is intentionally read-only. It rejects `workspace-write` and `danger-full-access`: a prompt or model-generated workflow cannot create its own Governor permit. Successful generated definitions are never saved automatically; `prompt-save-template --reviewed` is the explicit review boundary. See [`prompt-workflows.md`](skills/codex-workflows/references/prompt-workflows.md) for contracts, artifacts, restart behavior, and safety limits.

### Pinned project agents

`codex-exec-workflow.v2` binds tasks to managed project-scoped custom agents under `.codex/agents/`, following the [official Codex custom-agent contract](https://developers.openai.com/codex/subagents/). Each workflow records a byte-exact SHA-256 and its own TOML snapshot. Validation compares the project role, snapshot, digest, name, model, reasoning effort, and sandbox before planning or starting workers.

Create a role from external strict JSON or generate it through a separate read-only `codex exec` call:

```bash
python3 "$CLI" --project-root "$PROJECT" agent create reviewer \
  --spec reviewer.json \
  --model gpt-5.6-sol --reasoning-effort medium --sandbox read-only
python3 "$CLI" --project-root "$PROJECT" workflow bind-agent project:release-review \
  --task review --agent reviewer
```

Intentional updates repin every affected project workflow transactionally. Use `agent repin NAME` after direct TOML edits. All mutating role commands support `--dry-run` and `--json`; see [`project-agents.md`](skills/codex-workflows/references/project-agents.md) for both authoring modes, conflict behavior, drift recovery, and install commands.

The v2 built-in `adversarial-plugin-review` template installs three pinned read-only roles, runs six independent review lenses through `adversarial-reviewer`, challenges candidate findings, and produces a risk-ranked release verdict. Install it into project scope before planning:

```bash
python3 "$CLI" --project-root "$PROJECT" workflow install \
  builtin:adversarial-plugin-review --name adversarial-plugin-review
python3 "$CLI" --project-root "$PROJECT" workflow validate project:adversarial-plugin-review
python3 "$CLI" --project-root "$PROJECT" plan project:adversarial-plugin-review \
  --inputs .codex/exec-workflows/adversarial-plugin-review/example-inputs.json
```

The v2 built-in `workflow-audit` template audits any qualified project, user, or built-in workflow without running it. It validates and plans the target with real inputs, applies independent contract, execution, security, agent, loop, and usability lenses, challenges candidate findings, and produces a strict audit verdict:

```bash
python3 "$CLI" --project-root "$PROJECT" workflow install \
  builtin:workflow-audit --name workflow-audit
python3 "$CLI" --project-root "$PROJECT" workflow validate project:workflow-audit
python3 "$CLI" --project-root "$PROJECT" plan project:workflow-audit \
  --inputs .codex/exec-workflows/workflow-audit/example-inputs.json
python3 "$CLI" --project-root "$PROJECT" run project:workflow-audit \
  --inputs .codex/exec-workflows/workflow-audit/example-inputs.json --detach
```

Set `target-workflow` to an explicit `project:`, `user:`, or `builtin:` reference and provide its real input object in `target-inputs`. The audit itself is read-only and never executes the target.

Bundled model profiles are explicit: `adversarial-plugin-review` and `workflow-audit` use `gpt-5.6-sol` with `medium` reasoning; `fanout-synthesize`, `github-issue-worker`, `loop-monitor`, and new `workflow init` definitions use `gpt-5.6-luna` with `high` reasoning.

See [`skills/codex-workflows/SKILL.md`](skills/codex-workflows/SKILL.md) for the calling-agent method, [`workflow-format.md`](skills/codex-workflows/references/workflow-format.md) for the base contract, and [`loop-workflows.md`](skills/codex-workflows/references/loop-workflows.md) for recurring lifecycle and recovery semantics.

## Other skills

- `adaptive-deepening` and `graph-completion` provide evidence and knowledge-graph methods.
- `score-documentation-quality` performs adaptive documentation editing, evidence-backed scoring, and instruction migration. Its archive tool rejects recognizable secrets instead of preserving them in a repository archive.

## Security

The workflow CLI treats repository content, inputs, and upstream model outputs as untrusted data. Workflow IDs and generated paths are contained, role pins and snapshots are revalidated before execution, and private runtime state uses `0700` directories and `0600` files. Use the Codex sandbox and operating-system isolation as the enforcement boundary.

See [SECURITY.md](SECURITY.md) for the threat boundary and private reporting channel.

## Validation

```bash
python3 -m unittest discover -s tests -v
python3 skills/score-documentation-quality/scripts/test_scoring.py
```

Before distributing the skill separately, also run the `skill-creator` quick validator available in your Codex installation.

## License

MIT. See [LICENSE](LICENSE).
