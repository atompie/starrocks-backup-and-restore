# Design

## Context

`AGENTS.md`'s "Architectural boundaries" section defines the intended
flow as `HTTP API -> commands -> core operations -> data access layer`,
and names `src/starrocks_br/dal/metadata/` as the pattern for local
SQLAlchemy metadata access (already used by `jobs.py` and
`inventory_groups.py`: routes call `commands/`, `commands/` call
`dal/metadata/`, and only `dal/metadata/` touches `Session`/model
objects directly). `commands/clusters.py::delete_cluster` and every
route in `api/routes/clusters.py` currently skip that layer and run
`session.query(...)`/`db.query(...)`/`db.add(...)` themselves. This
change brings clusters in line with the jobs/inventory-groups
precedent; see proposal.md for the full rationale.

## Goals / Non-Goals

**Goals:**
- Every SQLAlchemy statement touching `Cluster` for cluster
  create/list/get/update/delete, and the `Job`/`Schedule` existence
  checks `delete_cluster` needs, lives in
  `src/starrocks_br/dal/metadata/clusters.py`.
- `commands/clusters.py` contains only orchestration/business rules
  (the active-job and enabled-schedule delete guards) and calls the new
  DAL module for everything else.
- `api/routes/clusters.py` and `_cluster_connect.get_cluster_or_404`
  contain only HTTP concerns (status codes, request/response shaping)
  and call `commands/clusters.py`.
- Identical externally observable behavior: same status codes
  (200/201/204/404/409), same response bodies, same exceptions raised
  to the route layer.

**Non-Goals:**
- Not touching `schedules.py`, `jobs.py`'s `get_job`/`get_job_history`,
  or any other route module that has the same DAL-boundary gap. Those
  are pre-existing instances of the same issue but are out of scope for
  this change, which is scoped to clusters per the request that raised
  it.
- Not changing the `Cluster` model, migrations, or the `ClusterCreate`/
  `ClusterUpdate`/`ClusterRead` schemas.
- Not changing transaction/session lifetime behavior (that is a
  separate, already-tracked gap per `AGENTS.md`'s "Parallel execution
  and metadata" section, concerning `commands/backup.py`, not clusters).

## Decisions

**New DAL functions (`dal/metadata/clusters.py`)**, mirroring the shape
of `dal/metadata/jobs.py` (plain functions taking `Session` first,
returning models or primitives, no HTTP-aware exceptions):

- `create(db, *, name, host, port, user, password_encrypted, default_backend) -> Cluster`
  builds the `Cluster`, adds, flushes, refreshes, returns it. Does not
  catch `IntegrityError` - that stays a route-level 409 translation
  concern (see below).
- `list_all(db) -> list[Cluster]` - replaces `list_clusters`'s
  `db.query(Cluster).order_by(Cluster.id).all()`.
- `get(db, cluster_id) -> Cluster | None` - replaces both
  `list_clusters`' implicit lookup pattern and
  `_cluster_connect.get_cluster_or_404`'s `db.get(Cluster, cluster_id)`.
- `update(db, cluster, updates: dict) -> Cluster` - applies
  `setattr` for each field, flushes, refreshes. The
  password-encryption/`password_provided` branching in
  `update_cluster` stays a command-layer concern (it is business logic:
  deciding *whether* to re-encrypt), so `update` just applies whatever
  dict of column values the command already computed - it takes
  `password_encrypted`, not `password`.
- `has_active_job(db, cluster_id) -> bool` and
  `has_enabled_schedule(db, cluster_id) -> bool` - lift the two queries
  straight out of the current `commands.clusters.delete_cluster` body.
- `delete(db, cluster) -> None` - `session.delete(cluster)`, matching
  the existing (no-flush; caller's transaction/session_scope flushes on
  commit, same as today).

**Where the `IntegrityError` (duplicate name) catch stays**: today
`api/routes/clusters.py::create_cluster` catches `IntegrityError` around
`db.flush()` and raises `HTTPException(409, ...)`. That is HTTP
translation of a DB-level constraint violation, not business logic, so
it is kept in the route, wrapping the call to
`commands.clusters.create_cluster` (which wraps
`dal.clusters.create`). This matches how
`api/routes/repositories.py::create_repository` catches
`exceptions.RepositoryAlreadyExistsError` (a domain exception raised by
the commands layer) at the route - the difference here is
`IntegrityError` is a SQLAlchemy exception, not a domain one, but the
translation point (route) is the same.

**`get_cluster_or_404` moves into `_cluster_connect.py` unchanged in
signature**, just swapping its body to call
`dal.metadata.clusters.get(db, cluster_id)`. It is not moved into
`commands/clusters.py` because it is a cross-route-module HTTP helper
(404 translation), shared by `clusters.py`, `repositories.py`,
`jobs.py`, and `schedules.py` - pulling it into commands would mean
either duplicating it per caller or having commands raise
`HTTPException`, both worse than today.

**Commands layer gets one function per route** (`create_cluster`,
`list_clusters`, `get_cluster`, `update_cluster`, plus the existing
`delete_cluster` rewritten to use the new guard helpers) rather than a
single generic CRUD passthrough, matching the one-function-per-use-case
shape already used in `commands/repositories.py` and `commands/jobs.py`.

## Risks / Trade-offs

- [Behavior drift during the mechanical move] → Each moved query is a
  direct lift (same filters, same ordering, same guard order in
  `delete_cluster`); existing tests in `test_commands_clusters.py` and
  `test_api_clusters.py` are run unmodified after the change to catch
  any regression, and new `tests/unit/crud/test_dal_clusters.py` pins
  the DAL functions individually.
- [`update`'s dict-of-columns shape leaks a "trust the caller" contract]
  → Scoped narrowly: only `commands.clusters.update_cluster` calls it,
  and it already computes the same `updates` dict today (from
  `payload.model_dump(exclude_unset=True)` plus the resolved
  `password_encrypted`), so the contract does not change, only which
  function executes the `setattr` loop.

## Open Questions

None - scope, approach, and file list are fixed by the proposal and the
existing jobs/inventory-groups precedent.
