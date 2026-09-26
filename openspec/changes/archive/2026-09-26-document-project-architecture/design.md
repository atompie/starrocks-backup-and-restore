## Context

See `proposal.md` for motivation and scope. The root `AGENTS.md` already describes the system, but the guide needs to be grounded in the current package structure and distinguish architectural rules from code paths that have not yet been brought into line.

The CLI and API use the shared commands layer for key operations, and the thread job backend dispatches through command handlers. The metadata layer is implemented with SQLAlchemy models and sessions under `store/`, with Alembic migrations. Current implementation details that affect the guide include direct inventory helper calls from API routes, direct helper calls during CLI initialization, and a cluster-scoped backup reservation in `concurrency.py` that currently prevents overlapping backup operations on one cluster. Also, `commands/backup.py` passes a SQLAlchemy session into `executor.execute_backup` while it submits and polls the long-running StarRocks backup, so the current call path does not yet meet the intended short-transaction separation.

## Goals / Non-Goals

**Goals:**

- Make `AGENTS.md` a concise map of project purpose, package responsibilities, major call paths, metadata storage, and API-convention sources.
- State the intended dependency direction: entry points call commands for application use cases; commands coordinate core operations; core operation modules do not call commands.
- Explain that metadata access is a distinct concern implemented with SQLAlchemy and Alembic, and state short metadata transactions as the intended design while accurately documenting the current backup execution gap.
- Describe concurrency without overstating current behavior: job execution supports worker concurrency, while backup reservations currently serialize backup work per cluster.

**Non-Goals:**

- Change backup concurrency policy or make backups overlap in runtime behavior.
- Refactor direct helper calls or change the commands, core, API, or data access layers.
- Duplicate API endpoint requirements already maintained in the OpenSpec specs.

## Decisions

### Keep the guide at the repository root

Update the root `AGENTS.md`, which is automatically discoverable as contributor and agent guidance. Alternatives such as a new architecture document under `docs/` or duplicating the content in the README would make the architectural constraints less visible at the point of code changes.

### Document intended boundaries and explicitly name current exceptions

Describe the commands boundary as the intended rule for application operations, then list known exceptions such as API inventory helper calls and CLI initialization helpers. This avoids teaching the exceptions as the desired architecture while keeping the guide accurate about the current code. Do not claim that every path already enters through commands.

### Separate the data access layer from operation flow

Use a compact diagram showing entry points, commands, core StarRocks operations, and the SQLAlchemy/Alembic metadata layer. Explain that metadata persistence is distinct from StarRocks backup execution. State that metadata transactions should be short and must not be held across long-running StarRocks work; identify the current backup executor session lifetime as an implementation gap to address when that code is changed. A broader rewrite to route every metadata call through a repository abstraction is outside this documentation change.

### Describe concurrency as policy plus execution capability

Explain the architectural need to isolate metadata writes so concurrent job execution does not hold shared database transactions open. Also state the code's actual backup policy: `reserve_job_slot` currently guards a cluster-wide `backup` scope, so two backup operations against the same cluster do not overlap. Do not turn the parallel-execution design principle into a claim that same-cluster backups currently run concurrently.

### Link API conventions to their maintained sources

Point to `openspec/specs/api-*/spec.md` and `src/starrocks_br/api/` for endpoint requirements and implementation patterns. This keeps detailed validation and response rules in the specs instead of copying a second, likely-to-drift checklist into `AGENTS.md`.

## Risks / Trade-offs

- [Risk] Architecture notes can become stale as boundaries evolve. -> Mitigation: name the source directories and current exceptions clearly, and update the guide when changing those boundaries.
- [Trade-off] The guide records a desired commands boundary that current code does not satisfy everywhere. -> Mitigation: distinguish the rule from the enumerated exceptions and avoid claiming full conformance.
- [Risk] Readers could confuse worker-pool concurrency with concurrent backups on the same cluster. -> Mitigation: state both the worker capability and current cluster-scoped reservation behavior explicitly.
