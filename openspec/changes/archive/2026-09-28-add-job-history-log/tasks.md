## 1. Schema migration

- [x] 1.1 Update `BackupHistory`/`RestoreHistory` models (`store/models.py`) to drop the
  `(cluster_id, label)` unique key and their current summary columns, and add `job_id` (FK to
  `jobs.id`, `ON DELETE CASCADE`), `ts`, `status`, `message` (nullable), `details_json`
  (nullable); verify by reading the updated model definitions.
- [x] 1.2 Add `label` (nullable, indexed) and `repository` (nullable) columns to `Job`
  (`store/models.py`), replacing `backup_history` as the source `restore.find_restore_pair`/
  `find_backup_repository` read from; verify by reading the updated model definition.
- [x] 1.3 Generate and hand-check an Alembic migration on top of `b7bf2d5f2365_baseline_schema`
  for the `backup_history`/`restore_history` column/constraint changes and the new `jobs`
  columns; verify with `alembic upgrade head` against a scratch SQLite DB and
  `alembic downgrade -1` round-tripping cleanly.

## 2. Append-only write path

- [x] 2.1 Add `history.append_backup_event(session_factory, job_id, status, message=None,
  details=None)` that opens its own short-lived session, reads the job's last recorded row, skips
  the insert if `status` is unchanged, otherwise inserts; verify with a unit test asserting no
  duplicate row on repeated identical status.
- [x] 2.2 Add `history.append_restore_event(...)` mirroring 2.1 for `restore_history`; same
  dedup-verifying unit test.
- [x] 2.3 Remove `history.log_backup`/`history.log_restore` (the old best-effort single-row
  writers) once nothing calls them; verify by grepping for callers and confirming none remain.

## 3. Wire StarRocks progress into history

- [x] 3.1 In `executor.execute_backup`, forward `poll_backup_status`'s `on_progress` callback into
  `append_backup_event` for each distinct state, and append a final `SUCCESS`/`FAILED` row when
  the job resolves (from `_extract_backup_progress`/`final_status`); verify with a unit test
  asserting the row sequence for a multi-state success and for a failure.
- [x] 3.2 In `restore.execute_restore_flow`, do the same for `poll_restore_status` and
  `append_restore_event`; same success/failure sequence unit tests.
- [x] 3.3 Confirm no session/transaction remains open across the poll loop itself (each append is
  its own short-lived session per AGENTS.md); verify by inspecting the session lifetime in the
  updated call sites and a test that a slow/failing StarRocks poll does not hold a DB transaction.
- [x] 3.4 Thread `job_id` from `jobs/thread_backend.py` through `JOB_HANDLERS`
  (`run_backup_full`/`run_backup_incremental`/`run_restore`) into `executor.execute_backup`/
  `restore.execute_restore_flow`, since both 3.1-3.3 and 3.5 need it; verify existing job-dispatch
  tests (`test_jobs_handlers.py`, `test_jobs_thread_backend.py`) still pass with the new
  signatures.
- [x] 3.5 `commands/jobs.submit_job` sets `Job.repository` from `params.get("repository")` at job
  creation (mirroring the existing `group_id` extraction); `run_backup_full`/
  `run_backup_incremental` set `Job.label` on the job row once `labels.determine_backup_label`
  computes it, via a short-lived session keyed by `job_id`; verify with a unit test that a
  submitted/completed backup job's `label`/`repository` columns are populated.
- [x] 3.6 Rewrite `restore.find_restore_pair`/`restore.find_backup_repository` to query `Job`
  (`cluster_id`, `label`, `job_type`, `status`, `finished_at`) instead of `backup_history`; update
  their existing unit tests (`tests/unit/service/test_restore.py`) to set up `Job` rows instead of
  `BackupHistory` rows.
- [x] 3.7 `labels.determine_backup_label`'s uniqueness check has the same dependency: it queries
  `backup_history` by `cluster_id`/`label` to avoid colliding with an existing label. Move it to
  query `Job.cluster_id`/`Job.label` (same catalog columns as 3.6); update its existing unit tests.
- [x] 3.8 `planner.find_latest_full_backup`/`find_recent_partitions` resolve an incremental
  backup's baseline by querying `backup_history` (`cluster_id`, `backup_type`, `status`, `label`,
  `finished_at`). Rewrite both to query `Job` (`job_type == "backup_full"` replacing
  `backup_type == "full"`) instead; update their existing unit tests.
- [x] 3.9 `prune.get_successful_backups` lists prunable snapshots by joining
  `BackupHistory`+`BackupPartition`+`TableInventory` on `label`/`cluster_id`/`status`/
  `finished_at`/`repository`. Rewrite the join to source those columns from `Job` instead.
  `prune.cleanup_backup_history` deletes the `BackupHistory` row for a pruned label - since that
  table is now an immutable execution log, rewrite it to delete the matching `Job` row instead
  (`cluster_id` + `label`), which cascades to that job's `backup_history` rows via the existing
  `ON DELETE CASCADE` (see design.md Decision "Pruning a snapshot deletes its `Job` row"). Update
  `tests/unit/service/test_prune.py` (or wherever these are covered) accordingly.

## 4. API

- [x] 4.1 Add `GET /job/{job_id}/history` (`api/routes/jobs.py`) returning the job's log — chosen
  by `job.job_type` — in time order, 404 for an unknown job id; add the response schema in
  `api/schemas.py`; verify with an integration-style unit test hitting the route for a backup job
  and a restore job.
- [x] 4.2 Sync `openspec/specs/api-job-execution/spec.md` with this change's delta spec once
  implementation lands (via `openspec-sync-specs` / archive).

## 5. Tests

- [x] 5.1 Unit test: a backup job's history contains one row per distinct StarRocks state plus a
  terminal `SUCCESS`/`FAILED` row, never duplicate consecutive rows for an unchanged state.
- [x] 5.2 Unit test: same for a restore job.
- [x] 5.3 Unit test: history rows are never updated or deleted after being written (append-only
  invariant) — attempt no update path exists, only insert.
- [x] 5.4 Unit test: `GET /job/{job_id}/history` for an unknown job id returns 404; for a job with
  no recorded history yet (e.g. still `PENDING`) returns an empty list with 200.
- [x] 5.5 Unit test: cluster-scoped history lookup via a join through `Job.cluster_id` returns
  only that cluster's jobs' history rows.
- [x] 5.6 Unit test: `find_restore_pair`/`find_backup_repository` resolve lineage/repository from
  `Job.label`/`Job.repository` (full backup, incremental with preceding full, label not found,
  no preceding full backup, unknown `job_type`, cluster-scoped).
- [x] 5.7 Full suite: `python -m pytest tests/unit` green.
