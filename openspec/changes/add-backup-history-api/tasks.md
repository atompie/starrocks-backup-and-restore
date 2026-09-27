## 1. Commands layer

- [x] 1.1 Add `list_jobs(db, cluster_id, job_type=None, status=None, limit=50, offset=0) -> list[Job]` to `src/starrocks_br/commands/jobs.py`, querying `Job` filtered by `cluster_id` and optional `job_type`/`status`, ordered by `created_at` descending, with `limit`/`offset`; verify with a unit test exercising filtering, ordering, and pagination.

## 2. API route

- [x] 2.1 Add `GET /backup/history/cluster/{cluster_id}` to `src/starrocks_br/api/routes/jobs.py`, using the existing `_get_cluster_or_404` helper, defaulting the job type filter to `backup_full`/`backup_incremental` with optional `job_type`/`status`/`limit`/`offset` query params, and returning `list[JobRead]`; verify by calling the endpoint against a cluster with no jobs, a cluster with jobs, and an unknown cluster id.

## 3. Tests

- [x] 3.1 Add `tests/unit/service/` coverage for the new endpoint: empty history, filtering by job_type/status, most-recent-first ordering with pagination, and 404 on an unknown cluster; verify with `pytest tests/unit/service/ -k backup_history`.

## 4. Validation

- [x] 4.1 Run `openspec validate add-backup-history-api --strict` and confirm it passes.
