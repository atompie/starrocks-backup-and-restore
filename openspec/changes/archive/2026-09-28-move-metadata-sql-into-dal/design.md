# Design

## Context

`AGENTS.md`'s "Architectural boundaries" section defines the intended
flow as `HTTP API -> commands -> core operations -> data access layer`,
and the merged `introduce-data-access-layer` change established
`src/starrocks_br/dal/db/` (StarRocks SQL) and
`src/starrocks_br/dal/metadata/` (local SQLAlchemy metadata access,
already used by `jobs.py`, `inventory_groups.py`, `history.py`,
`labels.py`) as the two DAL homes. That change deliberately left each
orchestration module's plain ORM queries in place, calling them
"business logic". This change corrects that: any code that builds a
SQLAlchemy `select`/`update`/`delete`, or calls
`session.execute`/`.query`/`.scalars`/`.scalar`/`.add`/`.delete`/`.get`
(or the route-layer `db.*` equivalents) against a `store.models` class,
moves into `dal/metadata/`; the calling module keeps only the decisions
(which query to run, what to do with the result, what exception to
raise) - see proposal.md for the full file-by-file rationale.

## Goals / Non-Goals

**Goals:**
- Every `store.models` SQLAlchemy statement outside `dal/metadata/`
  (excluding `store/`, `dal/` itself, migrations, and tests) is moved
  into a `dal/metadata/<module>.py` function.
- Each orchestration module (`commands/clusters.py`,
  `commands/schedules.py`, `commands/backup.py`, `prune.py`,
  `restore.py`, `planner.py`, `concurrency.py`,
  `jobs/thread_backend.py`) keeps its control flow and business rules,
  calling the new DAL functions instead of building queries itself.
- Route modules call only `commands/` (or the existing shared
  `_cluster_connect.py` helper, itself now backed by DAL) - no route
  gains a new direct `dal/metadata/` import that skips `commands/`.
- Identical externally observable behavior and identical job-status/
  concurrency/prune/restore semantics.

**Non-Goals:**
- Not touching `dal/db/` (StarRocks SQL) - already correctly isolated.
- Not changing any model, migration, or Pydantic schema.
- Not fixing the `commands/backup.py` open-session-across-StarRocks-call
  gap (`AGENTS.md`'s "Parallel execution and metadata" section) - a
  separate, already-tracked issue. Session lifetime/scope
  (`session_scope()` boundaries) stays exactly where it is today; only
  the query bodies move.
- Not consolidating `dal/metadata/restore_catalog.py` and
  `dal/metadata/backup_catalog.py` into one module - kept separate
  because `restore.py` and `planner.py` are separate orchestration
  modules with separate call sites and separate tests today, and merging
  their DAL modules would create a coupling that doesn't exist in the
  orchestration layer.

## Decisions

**One new DAL module per existing orchestration module, not one giant
`metadata.py`.** Mirrors the existing `dal/metadata/jobs.py` (paired
with `commands/jobs.py`) and `dal/metadata/labels.py` (paired with the
old `labels.py`) shape: a 1:1 (or close to it) mapping keeps call sites
easy to find and keeps each DAL module's tests scoped to one
`tests/unit/crud/test_dal_<module>.py` file.

**`dal/metadata/clusters.py`** - see the original cluster-scoped
version of this design for the full function list
(`create`/`list_all`/`get`/`update`/`has_active_job`/
`has_enabled_schedule`/`delete`); unchanged by the scope broadening.

**`dal/metadata/schedules.py`** functions: `create`, `list_for_cluster`,
`get`, `update`, `delete` (lifted from `api/routes/schedules.py`, which
today does all of this directly rather than through
`commands/schedules.py` - the route functions become thin: build
payload, call `commands.schedules.*`, translate exceptions to HTTP), and
`due_schedules`/`advance_next_run_at` (the `select(...).where(enabled,
next_run_at <= now)` and the conditional
`update(Schedule).where(id=..., next_run_at=previously_due_at)` from
`run_due_schedules`, preserving the exact conditional-UPDATE idempotency
mechanism described in that function's docstring - the DAL function
signature takes the previously-read `next_run_at` and returns the
`rowcount`, so `commands/schedules.py` keeps deciding what "0 rows
matched" means).

**`dal/metadata/jobs.py` (extended)**: add `get(db, job_id) -> Job |
None`, `mark_running`, `mark_progress`, `mark_failed`, `mark_success`
(each taking the fields `jobs/thread_backend.py` sets today - `status`,
`started_at`/`finished_at`, `state_detail`, `progress_pct`,
`error_message`, `result_json`) and `set_label` (for
`commands/backup.py::_set_job_label`). `jobs/thread_backend.py` keeps
its `session_scope()` blocks and control flow (submit to thread pool,
call the handler, catch exceptions) but each block's body becomes one
DAL call instead of a `session.get` + attribute writes. `get_job`/
`get_job_history` in `api/routes/jobs.py` route through
`commands.jobs.get_job`/a new `commands.jobs.get_job_history`, which
call `dal.metadata.jobs.get`/a new `dal.metadata.jobs.list_history_for_job`.

**`dal/metadata/prune.py`**: `get_successful_backups` (the flagged
`Job`/`BackupPartition`/`TableInventory` join) and
`cleanup_backup_history` (delete `BackupPartition` rows + the matching
`Job` row), moved verbatim from `prune.py`. `prune.py` keeps
`filter_snapshots_to_delete` (pure Python, no DB access) and its two
StarRocks pass-throughs (`verify_snapshot_exists`/
`execute_drop_snapshot`, already one-line delegations to `dal/db/prune.py`
since the prior change) unchanged - it becomes a thin orchestration
module with no direct SQLAlchemy import at all.

**`dal/metadata/restore_catalog.py`** and **`dal/metadata/backup_catalog.py`**:
direct lifts of `restore.py`'s and `planner.py`'s ORM query functions,
respectively (see proposal.md's "What Changes" for the exact function
list). `get_tables_from_backup` is split exactly as the prior change
already split its StarRocks half into `dal/db/restore.py` (per that
change's task 7.4) - this change does the matching split for its ORM
half, so after this change `restore.py::get_tables_from_backup` calls
both `dal/db/restore.py` (StarRocks `SHOW TABLES FROM`) and
`dal/metadata/restore_catalog.py` (the `BackupPartition` select) and
composes the two results, same as it composes them today.

**`dal/metadata/concurrency.py`**: `RunStatus` queries only
(`_get_active_jobs_for_scope`, `_insert_new_job`, `_cleanup_stale_job`,
`complete_job_slot`'s row lookup/write). `reserve_job_slot`'s conflict
resolution (`_handle_active_job_conflicts`, `_can_heal_stale_job`,
`_raise_concurrency_conflict`) stays in `concurrency.py` - it's decision
logic (does this scope conflict? can it be healed?), not data access,
and it already calls `concurrency_dal.is_backup_job_stale` (StarRocks
side, in `dal/db/concurrency.py`) via that same pattern.

**`_cluster_connect.get_cluster_or_404` and
`inventory_groups.get_inventory_group`** switch their one-line
`db.get(...)` to the corresponding DAL `get`, with no other change -
smallest possible fix for the two remaining raw lookups in the route
layer once `clusters.py` and `schedules.py` are fixed.

**Where HTTP/domain-exception translation stays**: unchanged from the
cluster-scoped design - `IntegrityError` -> 409 stays in
`api/routes/clusters.py::create_cluster` (DB-constraint translation is a
route concern); `ClusterHasActiveJobError`/`ConcurrencyConflictError`/etc.
continue to be raised by the commands/orchestration layer and caught by
routes, unchanged by where the underlying query now lives.

## Risks / Trade-offs

- [Large surface area increases regression risk versus the
  cluster-only version of this change] → Each moved query is a direct,
  mechanical lift (same filters, same ordering, same joins); the task
  list runs the existing test file for each touched module immediately
  after that module's extraction, not batched at the end, so a
  regression is caught at the commit that introduced it.
- [`jobs/thread_backend.py`'s per-call-site `session.get` writes are
  performance-sensitive (called on every progress update)] → The new
  `dal.metadata.jobs.mark_progress`/etc. functions are direct
  translations of the existing `session.get` + `setattr` + implicit
  flush-on-`session_scope`-exit; no new query, no new round trip.
- [Splitting `get_tables_from_backup` further increases the number of
  places that compose a StarRocks call with a DAL call] → Matches the
  precedent already set by the prior change for the same function; not
  a new pattern.

## Open Questions

None - scope, approach, and file list are fixed by the proposal and the
existing `dal/db` precedent from the merged `introduce-data-access-layer`
change.
