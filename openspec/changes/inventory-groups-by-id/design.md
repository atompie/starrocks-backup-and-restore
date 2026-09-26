## Context

See proposal.md - Why/What Changes for motivation and scope. Today `table_inventory.inventory_group`
and `schedules.group_name` are both plain `String(128)` columns with no shared table backing them -
"a group" is an emergent concept (rows sharing the same name for a `cluster_id`), not a row with its
own identity. This design covers how to introduce a real `inventory_groups` row and thread its `id`
through the schema, API, planner/restore/prune query paths, and both CLI surfaces
(`src/starrocks_br/cli.py` talking directly to the DB/StarRocks, and `src/starrocks_br/cli_api/*.py`
talking to the FastAPI server over HTTP).

## Goals / Non-Goals

**Goals:**
- Give inventory groups a surrogate `id`, analogous to `schedules.id`.
- Make every consumer (backup/restore/prune requests, job params, schedules, planner queries)
  reference a group by id.
- Preserve name-based ergonomics only at the CLI surface, via resolve-then-call.

**Non-Goals:**
- Data migration/backfill (no existing rows to migrate, per proposal.md).
- Renaming an existing group (out of scope; can be a follow-up if needed).
- Changing how table memberships (database/table pairs) themselves are modeled - only how they
  attach to a group.

## Decisions

### `inventory_groups` as a first-class table
Add `inventory_groups(id PK, cluster_id FK, name String(128), created_at, updated_at)` with a
`UniqueConstraint(cluster_id, name)`. This mirrors `schedules`/`clusters` conventions already in
`store/models.py` (surrogate integer PK, `cluster_id` FK, timestamps).

Alternative considered: give `table_inventory` rows a self-referential "canonical row" instead of a
separate table. Rejected - it conflates membership rows with group identity and doesn't give
`schedules` a clean FK target.

### `table_inventory.inventory_group` (str) -> `inventory_group_id` (FK, NOT NULL)
The existing `UniqueConstraint(cluster_id, inventory_group, database_name, table_name)` becomes
`UniqueConstraint(cluster_id, inventory_group_id, database_name, table_name)`, and
`Index(ix_table_inventory_cluster_group)` becomes an index on `(cluster_id, inventory_group_id)`.
`cluster_id` stays on `table_inventory` (denormalized from `inventory_groups.cluster_id`) to keep
the existing per-cluster query/index shape rather than requiring a join for every lookup.

### `schedules.group_name` (str) -> `inventory_group_id` (FK, NOT NULL)
Same rationale as above. Deleting an inventory group that one or more schedules still reference is
rejected with a conflict (HTTP 409) rather than cascading the delete to those schedules or setting
the FK null - silently orphaning or deleting a schedule as a side effect of an unrelated group
deletion would be a surprising, hard-to-audit behavior. Callers that want to delete the group must
delete or repoint its schedules first. Enforced with `ondelete="RESTRICT"` on the FK plus an
application-level check in the delete-group route (SQLite's FK enforcement is session-dependent, so
the route checks explicitly rather than relying solely on the DB constraint).

### One new Alembic migration, not a history rewrite
Even though this local database has no rows yet, the two existing migrations
(`9bf7a7f90f78_initial_schema.py`, `059d2b79d525_move_ops_tables_into_sqlite.py`) are already
committed history that any other environment migrates through from scratch. This change adds a new
revision on top (create `inventory_groups`; alter `table_inventory` and `schedules`) rather than
editing the two existing migration files, so migration history stays linear and replayable
regardless of what state any given environment is in. Because there is no data to preserve, the new
migration drops and recreates the affected columns/constraints directly rather than writing
backfill logic.

### CLI name resolution happens client-side, in both CLI surfaces, against the id-only API/DB
No name-based lookup route or query is kept anywhere below the CLI. Each CLI surface resolves
`--group <name>` to an id itself, right before constructing the request that needs it:
- `src/starrocks_br/cli.py` (direct-to-StarRocks path): resolves via a new
  `inventory_groups.get_group_id_by_name(session, cluster_id, name)` helper that queries
  `inventory_groups` directly, since this CLI path already holds a DB session.
- `src/starrocks_br/cli_api/*.py` (HTTP client path): has no DB session, only the REST API. It
  resolves by calling the existing list-groups endpoint (`GET /clusters/{id}/groups`, now returning
  `id` + `name` per group) and matching the supplied name client-side. This avoids adding a
  name-based lookup endpoint back into the API surface, which would undercut the point of moving
  the API to id-keyed paths.

Both paths raise the same category of user-facing error (non-zero exit, message naming the
cluster and the unresolved group name) when no match is found, before any job/schedule is created.

Alternative considered: keep a `GET /clusters/{id}/groups/by-name/{name}` convenience endpoint
so the HTTP CLI doesn't need to list-and-match. Rejected for now - it re-introduces a name-keyed
route contrary to the proposal's "break compatibility, id in the URL" decision; the list-and-match
approach is a few extra lines in the CLI client and keeps the API surface purely id-based. Flagged
as an open question below in case list size ever makes this matter.

### Group creation still takes a name, returns an id
`POST /clusters/{id}/groups` keeps accepting `name` (plus initial memberships) in the request body -
names remain how humans create and refer to groups conversationally - but the response and every
subsequent route uses `id`. `inventory_groups.create_group` is refactored to insert the
`inventory_groups` row first (or reuse it if `config init`'s YAML bootstrap already created it) and
then insert `table_inventory` rows against the resulting id.

## Risks / Trade-offs

- **CLI list-and-match cost**: resolving a name via a full group list is O(groups on the cluster)
  per CLI invocation. Acceptable given expected group counts (tens, not thousands) per cluster;
  revisit if that assumption changes.
- **Breaking change with no deprecation window**: every existing name-based request shape (API
  bodies, CLI-to-API payloads, job `params_json`) stops working the moment this ships. Acceptable
  per proposal.md, since no data or external clients exist yet, but this is the last point at which
  a compatibility shim would be cheap to add if that assumption turns out to be wrong.
- **RESTRICT on schedule deletion**: an operator deleting a group must first notice and clean up any
  schedules referencing it. Mitigation: the group-delete route's 409 response should name the
  blocking schedule id(s) so the operator isn't left guessing.

## Migration Plan

1. Add the new Alembic revision (`inventory_groups` table; `table_inventory.inventory_group_id`;
   `schedules.inventory_group_id`; drop the old string columns and their constraints/indexes).
2. Update models, `inventory_groups.py`, planner/restore/prune, API schemas/routes, command layer,
   and both CLI surfaces together (see tasks.md) - there is no intermediate state where old and new
   fields coexist, since there's no data requiring a phased rollout.
3. Update all affected tests to the new id-based contracts.
4. Run `alembic upgrade head` against a fresh (empty) database as the acceptance check; no rollback
   data-preservation path is needed given there's nothing to preserve. `alembic downgrade -1` should
   still cleanly reverse the new revision for symmetry, even though it won't be exercised with real
   data.

## Open Questions

- If group counts per cluster ever grow large enough that client-side list-and-match becomes a real
  cost in the HTTP CLI path, reconsider adding a narrow server-side name-lookup helper endpoint. Not
  worth solving speculatively now.
