# Documentation Artifact Rules

## Constitution

1. Govern only durable documentation and instruction artifacts in `BearsCLOUD/codex-workflow-governor`.
2. Keep one editable owner for each durable fact or rule.
3. Do not own product meaning or development workflow policy here.
4. Preserve unique meaning, links, schemas, recovery paths, and evidence until the owning lifecycle permits removal.
5. Place each artifact at the nearest complete scope that owns its meaning.
6. Do not duplicate a rule or durable fact across authorities.
7. Never store secrets, credentials, personal data, mutable state, or runtime snapshots in instruction artifacts.
8. Create a byte-exact archive with provenance before retiring an instruction artifact.
9. Treat size, counts, and reachability as review signals rather than deletion authority.
10. Verify placement, ownership, links, anchors, and Markdown format after every instruction change.

## Scope

- Apply this file to documentation, instruction artifacts, archives, links, and durable technical references.

## Artifact types

| Artifact | Owns |
| --- | --- |
| `AGENTS.md` | Constitution, repository scope, precedence, and routing |
| `MODEL.md` | Project model behavior |
| `DOCS.md` | Documentation authority, placement, lifecycle, and formatting |
| `WORKFLOW.md` | Development workflow policy and delivery procedure routing |
| Project skill | One repeatable procedure and no policy authority |
| Repository document | Architecture, API, algorithm, migration, runbook, or technical reference |
| Executable artifact | Source, schema, manifest, migration, test, or generated evidence |

<!-- owner:docs-lifecycle -->
## Lifecycle

- Archive exact removed text outside active instruction-loading paths and record its source revision, Git blob, SHA-256, reason, and replacement.
- Retire an artifact only when it is stale, a no-op, unreachable, or replaced with verified behavioral parity.
- Use Git history for ordinary recovery and a named archive for audit, incident, legal, acceptance, or rollback evidence.
- Never load, execute, build, test, package, deliver, migrate, or operate active behavior from an archive.
- Never archive secrets, credentials, private keys, personal data, or legally restricted material.

<!-- owner:docs-placement -->
## Placement and naming

- Keep top-level instructions at the root of the governed repository.
- Keep project procedures in `skills/<name>/SKILL.md` and route repeatable execution through the approved governor skill.
- Keep the instruction chain at the repository root and add nested `AGENTS.md` only for a distinct local scope.
- Use `AGENTS.md`, `MODEL.md`, `DOCS.md`, and `WORKFLOW.md` for repository top-level authorities.
- Use lowercase kebab-case names for linked skills and repository documents where the surrounding convention permits it.

## Terms

- An `instruction artifact` defines or routes agent behavior in a declared scope.
- A `project skill` owns one repeatable procedure and does not become a policy authority.
- An `archive` is non-authoritative byte-exact evidence retained outside active loading paths.

## Linking

- Link only authority, ownership, dependency, procedure, or evidence required by the reader.
- Keep root [AGENTS.md](AGENTS.md) limited to its three peer authority routes.
- Link procedures from [WORKFLOW.md](WORKFLOW.md) and repair backlinks before retiring an authority.
- Prefer stable relative links and anchors over copied text.

## Markdown

- Use exactly one H1 and H2 headings for independent domains.
- Use H3 only for scoped subtypes, exceptions, or examples.
- Use numbered lists for mandatory order, tables for one consistent schema, and fenced blocks only for exact copyable content with a declared language.
- Prefix every callout body line with `>` and use links for authority or evidence.
