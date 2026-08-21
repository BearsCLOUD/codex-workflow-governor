# Project Agents

Project agents are managed Codex custom-agent files under `.codex/agents/`. They follow the official Codex custom-agent format while deliberately allowing exactly six top-level TOML fields: `name`, `description`, `developer_instructions`, `model`, `model_reasoning_effort`, and `sandbox_mode`. Other custom-agent files may coexist in the directory, but a workflow v2 pin can bind only a role that satisfies this managed subset.

## Choose an authoring path

Use `--spec` when another agent or person already prepared a strict JSON object with exactly `name`, `description`, and `developer_instructions`:

```bash
python3 "$CLI" --project-root "$PROJECT" agent create reviewer \
  --spec reviewer.json \
  --model gpt-5.6-sol --reasoning-effort medium --sandbox read-only
```

Use `--generate` to have the installed `codex exec` author the same strict JSON shape. Generator settings are separate from the role's pinned target settings, and generation always runs read-only:

```bash
python3 "$CLI" --project-root "$PROJECT" agent create reviewer \
  --generate "Review release blockers with exact repository evidence." \
  --generator-model gpt-5.6-luna --generator-reasoning-effort medium \
  --model gpt-5.6-sol --reasoning-effort medium --sandbox read-only
```

For `create`, all three target settings are required and an existing role is never overwritten. For `update`, omitted target settings retain their current values. `update` rechecks the original role bytes after generation and fails without writing if the role changed concurrently. Every mutating agent command supports `--dry-run` and `--json`.

## Inspect and register

```bash
python3 "$CLI" --project-root "$PROJECT" agent list
python3 "$CLI" --project-root "$PROJECT" agent show reviewer
python3 "$CLI" --project-root "$PROJECT" agent validate reviewer
python3 "$CLI" --project-root "$PROJECT" agent schema
python3 "$CLI" --project-root "$PROJECT" agent register /path/to/reviewer.toml
```

`register` validates a complete six-field TOML file and imports it atomically. A filename need not match at the source location; the validated `name` determines the project target.

## Bind, drift, and repin

```bash
python3 "$CLI" --project-root "$PROJECT" workflow bind-agent project:release-review \
  --task review --agent reviewer
```

Binding upgrades v1 to v2 when needed, adds the pin and workflow snapshot, assigns `agent`, and removes task-level model, reasoning, and sandbox values. A bound task receives these values only from the pin.

Validation compares the project TOML, workflow snapshot, recorded byte-exact SHA-256, role name, model, reasoning effort, and sandbox. Paths are fixed and contained without symlink aliases. Any drift blocks `plan` and `run` before a worker starts.

After an intentional manual TOML edit, synchronize every project workflow that pins the role:

```bash
python3 "$CLI" --project-root "$PROJECT" agent repin reviewer --dry-run --json
python3 "$CLI" --project-root "$PROJECT" agent repin reviewer
```

`agent update` performs this repin automatically. All affected workflow JSON files and role snapshots are staged and validated before replacement; an ordinary write error restores byte-exact preimages. A process crash can still interrupt a multi-file replacement, and the next workflow validation detects that partial state as drift.

## Install a bundled workflow

```bash
python3 "$CLI" --project-root "$PROJECT" workflow install \
  builtin:adversarial-plugin-review --name adversarial-plugin-review
```

Installation materializes the built-in under `.codex/exec-workflows/` and registers its bundled roles. Byte-identical existing roles are reused. A conflicting project role blocks installation; `--replace-agents` explicitly replaces conflicting roles and repins other affected project workflows. Inspect `--dry-run --json` before intentional replacement.

After install or repin, always validate and inspect the full resolved plan before running:

```bash
python3 "$CLI" --project-root "$PROJECT" workflow validate project:adversarial-plugin-review
python3 "$CLI" --project-root "$PROJECT" plan project:adversarial-plugin-review --inputs inputs.json
```
