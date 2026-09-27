## Why

`GET /backup/history/cluster/{cluster_id}` currently only filters by `job_type`, `status`, and pagination. Operators cannot narrow the listing to a single job's record or to the jobs belonging to one inventory group, forcing them to page through the full cluster history to find what they need.

## What Changes

- Add an optional `job_id` query parameter to `GET /backup/history/cluster/{cluster_id}` that narrows the listing to a single job.
- Add an optional `group_id` query parameter to the same endpoint that narrows the listing to jobs submitted for that inventory group.
- Add a `group_id` column to the `jobs` table (nullable, indexed) populated at job-submission time from whatever `group_id` value is present in the submitted job's params (`backup_full`, `backup_incremental`, and `prune` always carry one; `restore` carries one only when set), via an Alembic migration. Existing rows are left `NULL` (no backfill); `group_id` filtering only applies to jobs submitted after the migration.
- `group_id` filtering has no effect on jobs whose params carried no group id (their `group_id` column is `NULL`, so they simply won't match a `group_id` filter).

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `api-job-execution`: the "Backup job history can be listed for a cluster" requirement gains `job_id` and `group_id` filter parameters alongside the existing `job_type`/`status`/pagination filters.

## Impact

- `src/starrocks_br/store/models.py`: add `group_id` column to `Job`.
- Alembic migration adding the new nullable, indexed `jobs.group_id` column.
- `src/starrocks_br/commands/jobs.py`: `submit_job` persists `group_id` as a real column whenever the submitted `params` carries one (in addition to `params_json`); `list_jobs` accepts and applies `job_id`/`group_id` filters.
- `src/starrocks_br/api/routes/jobs.py`: `list_backup_history` accepts and forwards the new `job_id`/`group_id` query parameters.
- `openspec/specs/api-job-execution/spec.md`: new scenarios for filtering by `job_id` and by `group_id`.
