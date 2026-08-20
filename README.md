# Codex Workflow Governor

Codex Workflow Governor provides two deliberately separate execution models:

- governed native-Agent workflow definitions whose runtime locks, hooks, permits, and lifecycle tools are supplied separately from this plugin;
- reusable asynchronous `codex exec` workflows with bounded fan-out, explicit data dependencies, strict JSON outputs, detached execution, and persisted artifacts.

The implementation uses only the Python standard library. It does not require the OpenAI Agents SDK, LangGraph, CrewAI, Temporal, or a web service.

## Requirements

- Linux or macOS with Python 3.11 or newer;
- a current Codex CLI installation authenticated with `codex login`;
- a Git worktree for governed repository mutations.

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

Reusable workflow scopes are:

- project: `.codex/exec-workflows/<name>/`;
- user: `$CODEX_HOME/exec-workflows/<name>/`;
- built-in: `skills/codex-workflows/assets/workflows/<name>/`.

Each task has a strict root-object JSON Schema. The runner invokes `codex exec --ephemeral --json --output-schema ... --output-last-message ...`, independently validates the saved final JSON, and preserves prompts, JSONL events, stderr, attempts, task results, and run state outside the target repository. Read-only workers run concurrently up to `max_parallel`; write-capable workers require an explicit run flag and are serialized project-wide, including across runs.

The supervisor watches Codex JSONL events while the child is live. After `turn.completed`, `turn.failed`, or `turn.cancelled`, it allows a two-second output-flush grace period, then reconciles the terminal event, process group, exit state, and strict final output. Missing, malformed, and schema-invalid outputs receive distinct stable failure metadata; retries remain bounded by the task contract. A restarted worker resumes durable attempt state, cleans up recorded orphan process groups, and cannot consume the same retry twice. `CODEX_WORKFLOWS_TERMINAL_GRACE_SECONDS` may set a bounded `0.05`–`60` second grace for testing or host-specific flushing.

`plan` discloses the workflow digest, sandbox, working directory, model settings, timeouts, retries, fan-out bounds, and conservative call count. The default budget is 5000 model calls; higher budgets and write-capable sandboxes require explicit command-line opt-ins. Persisted inputs and artifacts may contain sensitive data, so do not pass credentials or unnecessary personal information.

The graph, readiness, dependency blocking, fan-out item order, and artifact layout are deterministic for fixed workflow files and inputs. Model text and semantics are not deterministic.

### Pinned project agents

`codex-exec-workflow.v2` binds tasks to managed project-scoped custom agents under `.codex/agents/`, following the [official Codex custom-agent contract](https://developers.openai.com/codex/subagents/). Each workflow records a byte-exact SHA-256 and its own TOML snapshot. Validation compares the project role, snapshot, digest, name, model, reasoning effort, and sandbox before planning or starting workers.

Create a role from external strict JSON or generate it through a separate read-only `codex exec` call:

```bash
python3 "$CLI" --project-root "$PROJECT" agent create reviewer \
  --spec reviewer.json \
  --model gpt-5.6-sol --reasoning-effort xhigh --sandbox read-only
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

See [`skills/codex-workflows/SKILL.md`](skills/codex-workflows/SKILL.md) for the calling-agent method and [`workflow-format.md`](skills/codex-workflows/references/workflow-format.md) for the contract.

## Governed native-Agent workflows

Governed source graphs live under `.codex/workflows/<workflow-id>/workflow.json`. Apply creates an immutable `workflow.lock.json`, generated Mermaid and Markdown views, and verified role files. Drafts, permits, results, and run state stay under `PLUGIN_DATA`.

The explicit lifecycle skills are `workflow-create`, `workflow-check`, `workflow-analyze`, `workflow-update`, `workflow-apply`, `workflow-visualize`, and `workflow-run`. This plugin does not register lifecycle hooks. When the lifecycle runtime and trusted hooks are supplied separately, guarded execution is reported only while native dispatch inputs are observable; otherwise the runtime records the run as advisory.

The async exec format is not a silent reinterpretation of the governed v1 lock format. Choose one backend for one workflow.

## Other skills

- `adaptive-deepening` and `graph-completion` provide evidence and knowledge-graph methods.
- `score-documentation-quality` performs adaptive documentation editing, evidence-backed scoring, and instruction migration. Its archive tool rejects recognizable secrets instead of preserving them in a repository archive.

## Security

This plugin does not register MCP servers or lifecycle hooks. If the native-Agent lifecycle runtime is supplied separately, its reader surface should remain read-only and its maintainer surface should be used only for an explicit user-requested lifecycle action. Workflow IDs and generated paths are contained, guarded stops revalidate lock authority, and private runtime state uses `0700` directories and `0600` files.

`allowed_paths` in native dispatch packets is compliance metadata, not an operating-system filesystem sandbox. Use the Codex sandbox and operating-system isolation as the enforcement boundary.

See [SECURITY.md](SECURITY.md) for the threat boundary and private reporting channel.

## Validation

```bash
python3 -m unittest discover -s tests -v
python3 skills/score-documentation-quality/scripts/test_scoring.py
```

Before distributing the skill separately, also run the `skill-creator` quick validator available in your Codex installation.

The separate plugin-validator rejection for marketplace `policy.products` is not changed by the project-agent feature and remains an independent release blocker until the marketplace policy contract is resolved.

## License

MIT. See [LICENSE](LICENSE).
