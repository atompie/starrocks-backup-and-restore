## Why

Operators can currently poll one job's status via `GET /job/{job_id}`, but
there is no way to list past backups for a cluster to see recent runs, their
status, and progress at a glance — a "backup log" view. This is needed to
answer a basic operational question ("what backups have run recently, and
did they succeed?") without knowing individual job ids in advance.

## What Changes

- Add a paginated `GET /backup/history/cluster/{cluster_id}` endpoint that
  lists backup jobs (job type `backup_full`/`backup_incremental` by default,
  optionally filtered to a different job type) for a cluster, most recent
  first, each with its current status and progress.
- Support optional `job_type` and `status` query filters, and `limit`/`offset`
  pagination.
- Reuse the existing `Job` model and `JobRead` response schema — no new
  model or schema is introduced.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `api-job-execution`: adds a requirement that backup job history for a
  cluster can be listed (most recent first, with status and progress),
  alongside the existing single-job status-polling requirement.

## Impact

- `src/starrocks_br/commands/jobs.py`: new `list_jobs` command function.
- `src/starrocks_br/api/routes/jobs.py`: new `GET` route.
- No schema, model, or migration changes — `Job` and `JobRead` already carry
  every field needed (status, progress_pct, timestamps, error_message).
