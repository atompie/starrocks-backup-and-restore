## Context

`GET /backup/history/cluster/{cluster_id}` (`src/starrocks_br/api/routes/jobs.py`) reads the `jobs` table via `commands/jobs.py::list_jobs`, not the separate `backup_history`/`restore_history` bookkeeping tables (those are unrelated StarRocks-execution logs written by `history.py`). The `Job` model (`src/starrocks_br/store/models.py`) has no `group_id` column; `group_id` is only present inside the opaque `params_json` blob set by `commands/jobs.py::submit_job`. Several job types carry a `group_id` in their params: `backup_full` and `backup_incremental` (required), `prune` (required), and `restore` (optional, `None` when the request targets a specific table instead of a group). See proposal.md for motivation.

## Goals / Non-Goals

**Goals:**
- Let a client filter the backup history listing down to one job (`job_id`) or to one inventory group's jobs (`group_id`).
- Keep `group_id` filterable with a plain indexed SQL column rather than parsing `params_json` at query time.

**Non-Goals:**
- No backfill of `group_id` for jobs submitted before this change; those rows keep `group_id = NULL` and are simply excluded by a `group_id` filter.
- No change to `BackupHistory`/`RestoreHistory` tables or to `history.py` - they are not involved in this endpoint.
- No change to job submission semantics beyond persisting `group_id` as a column in addition to its existing place in `params_json` (kept for backward compatibility with any other code reading `params_json`).

## Decisions

- **Add `group_id: int | None` as a real column on `Job`, indexed**, populated by `submit_job` at creation time from the same value already passed into `params_json`, rather than parsing `params_json` at query time. Rationale: matches how `job_type`/`status` are already implemented as first-class filterable columns, is indexable, and avoids coupling the query layer to `params_json`'s internal shape. Alternative considered: a JSON-extract expression in the query - rejected as unindexed, backend-specific (SQLite vs. Postgres JSON functions differ), and fragile if `params_json`'s shape changes.
- **`group_id` is nullable and populated generically from `params.get("group_id")`, whatever the job type**, rather than special-cased to `backup_full`/`backup_incremental`. Investigation during implementation found `prune` also carries a required, non-null `group_id`, and `restore` carries an optional one (`None` when the request targets a specific table instead of a group). Reading it generically means `submit_job` needs no job-type-specific branching, and `group_id` filtering works consistently for any job type whose params happen to carry one - job types with no `group_id` key simply leave the column `NULL` and are excluded by a `group_id` filter.
- **`job_id` filter matches the `Job.id` primary key directly** (`where(Job.id == job_id)`); no schema change needed.
- **No backfill migration for existing rows.** Rationale: the group id for historical jobs is only recoverable by parsing `params_json`, which the proposal deliberately avoids doing as a query-time operation; doing it once at migration time as a one-off backfill script was considered but rejected to keep the migration simple and because older history is less likely to need group-scoped lookups.

## Risks / Trade-offs

- [Older jobs won't match a `group_id` filter, only a full-cluster or `job_id` lookup still works for them] → Documented behavior (see spec scenario "excludes jobs submitted before the filter was supported"); acceptable per proposal decision.
- [Storing `group_id` in two places (`params_json` and the new column) risks drift if future code updates one but not the other] → Only `submit_job` writes both, at the same time, from the same input value; no other write path touches either.

## Migration Plan

- Alembic migration: add nullable `group_id` column (integer) to `jobs` table with an index; no data backfill, no default value beyond `NULL`.
- Rollback: drop the column/index; safe since no other code depends on it existing.
