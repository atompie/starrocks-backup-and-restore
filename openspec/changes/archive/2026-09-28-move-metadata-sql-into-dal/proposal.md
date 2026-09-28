# Proposal

## Why

`AGENTS.md`'s "Architectural boundaries" section requires that persistent
metadata access go through the data access layer, and the merged
`introduce-data-access-layer` change (PR #2) already moved every
*StarRocks-facing* SQL string into `src/starrocks_br/dal/db/`. It
explicitly left each module's SQLAlchemy ORM queries against the local
metastore (`Cluster`, `Job`, `Schedule`, `RunStatus`, `BackupPartition`,
`TableInventory`, `InventoryGroup`) in place, treating them as
"orchestration". A follow-up audit (started from a report that
`api/routes/clusters.py::list_clusters` runs a raw `db.query(Cluster)`,
and `commands/clusters.py::delete_cluster` runs `session.query(Job)`/
`session.query(Schedule)` directly) found the same pattern repeated
across most of `src/starrocks_br/`: `session.execute`/`session.query`/
`session.scalars`/`session.add`/`session.delete`/`session.get` (and the
route-layer `db.*` equivalents) called directly instead of through
`src/starrocks_br/dal/metadata/`. `dal/metadata/` already holds this kind
of code for `jobs`, `inventory_groups`, `history`, and `labels` - the gap
is that `clusters`, `schedules`, `restore`, `planner`, `prune`,
`concurrency`, `jobs/thread_backend`, and several route handlers were
never brought into it.

## What Changes

- Add `src/starrocks_br/dal/metadata/clusters.py`: `Cluster` create,
  list, get, update, delete, plus the `Job`/`Schedule` existence checks
  `commands/clusters.py::delete_cluster` currently runs inline
  (`session.query(Job)...`, `session.query(Schedule)...`).
- Add `src/starrocks_br/dal/metadata/schedules.py`: `Schedule` create,
  list, get, update, delete, and the conditional-`UPDATE`/dispatch query
  used by `run_due_schedules` - lifted from `commands/schedules.py`
  (`session.query(Schedule)`, `session.execute(update(Schedule)...)`,
  `session.get(Cluster, ...)`) and from `api/routes/schedules.py`, which
  duplicates the same CRUD directly in the route (`db.get`, `db.add`,
  `db.flush`, `db.query(Schedule)`, `db.delete`).
- Extend `src/starrocks_br/dal/metadata/jobs.py` with `get_job` and the
  job-status-transition writes currently duplicated in
  `jobs/thread_backend.py` (`_make_progress_callback`, `_run_job`: four
  `session.get(Job, job_id)` call sites that set `status`/
  `state_detail`/`progress_pct`/`error_message`/`result_json`/
  `finished_at`) and in `commands/backup.py::_set_job_label`
  (`session.get(Job, job_id)`), and with the two `db.get(Job, job_id)` +
  `db.scalars(select(...))` lookups in `api/routes/jobs.py`
  (`get_job`, `get_job_history`).
- Add `src/starrocks_br/dal/metadata/prune.py`: `get_successful_backups`'s
  `Job`/`BackupPartition`/`TableInventory` join query and
  `cleanup_backup_history`'s delete/lookup, moved out of
  `src/starrocks_br/prune.py` (the `session.execute(select(...))` the
  user flagged, plus its `session.scalars`/`session.delete` neighbors),
  leaving `filter_snapshots_to_delete` (pure logic) and the
  `verify_snapshot_exists`/`execute_drop_snapshot` StarRocks pass-throughs
  (already routed through `dal/db/prune.py`) in place.
- Add `src/starrocks_br/dal/metadata/restore_catalog.py`: the ORM lookups
  in `src/starrocks_br/restore.py` - `find_restore_pair`,
  `find_backup_repository`, `get_tables_from_backup`'s
  `session.execute(select(BackupPartition...))` half (its StarRocks half
  already lives in `dal/db/restore.py`), and `get_partitions_from_backup`.
- Add `src/starrocks_br/dal/metadata/backup_catalog.py`: the ORM lookups
  in `src/starrocks_br/planner.py` - `find_latest_full_backup`,
  `find_tables_by_group`, `find_recent_partitions`'s baseline-job lookup,
  and `record_backup_partitions`.
- Add `src/starrocks_br/dal/metadata/concurrency.py`: the `RunStatus`
  queries in `src/starrocks_br/concurrency.py` -
  `_get_active_jobs_for_scope`, `_insert_new_job`, `_cleanup_stale_job`,
  `complete_job_slot` - leaving `reserve_job_slot`'s conflict-resolution
  control flow (calls into the new DAL functions) in place.
- Update `api/routes/_cluster_connect.py::get_cluster_or_404` and
  `api/routes/inventory_groups.py::get_inventory_group` to resolve
  through the new/existing DAL `get` functions instead of
  `db.get(Cluster, ...)` / `db.get(InventoryGroup, ...)` directly.
- Every touched route/command function keeps calling through
  `commands/`; no route gets a new direct DAL import that bypasses
  `commands/` (routes call commands, commands call the new
  `dal/metadata/` modules), matching the pattern already established by
  `commands/jobs.py` -> `dal/metadata/jobs.py`.
- No behavioral change: HTTP status codes, response bodies, error types
  (`ClusterHasActiveJobError`, `ClusterHasEnabledScheduleError`,
  `ConcurrencyConflictError`, etc.), job-status transitions, and prune/
  restore/backup query results are preserved exactly - this is an
  internal layering refactor, not a feature or bugfix change.

## Capabilities

### New Capabilities

(none - no new user-facing behavior)

### Modified Capabilities

(none - `openspec/specs/api-*/spec.md`'s externally observable behavior
is unchanged; this is an internal layering refactor)

## Impact

- New: `src/starrocks_br/dal/metadata/clusters.py`,
  `dal/metadata/schedules.py`, `dal/metadata/prune.py`,
  `dal/metadata/restore_catalog.py`, `dal/metadata/backup_catalog.py`,
  `dal/metadata/concurrency.py`.
- Extended: `src/starrocks_br/dal/metadata/jobs.py`.
- Modified (calls moved out, orchestration kept):
  `src/starrocks_br/commands/clusters.py`,
  `src/starrocks_br/commands/schedules.py`,
  `src/starrocks_br/commands/backup.py`,
  `src/starrocks_br/api/routes/clusters.py`,
  `src/starrocks_br/api/routes/schedules.py`,
  `src/starrocks_br/api/routes/jobs.py`,
  `src/starrocks_br/api/routes/_cluster_connect.py`,
  `src/starrocks_br/api/routes/inventory_groups.py`,
  `src/starrocks_br/jobs/thread_backend.py`,
  `src/starrocks_br/prune.py`, `src/starrocks_br/restore.py`,
  `src/starrocks_br/planner.py`, `src/starrocks_br/concurrency.py`.
- Not affected: `src/starrocks_br/dal/db/` (already-completed StarRocks
  SQL layer), `src/starrocks_br/store/` (models/session/crypto),
  `commands/repositories.py`/`commands/prune.py`/`commands/restore.py`
  (already orchestration-only, calling `dal/db` and the modules above -
  no direct SQLAlchemy access of their own), the transaction-lifetime gap
  in `commands/backup.py` holding a session open across
  `executor.execute_backup` (a separate, already-tracked architectural
  issue per `AGENTS.md`, not fixed by this change).
- Existing tests continue to exercise the same public functions/routes
  and should pass unmodified; new `tests/unit/crud/` tests cover each new
  DAL module directly (see tasks.md).
