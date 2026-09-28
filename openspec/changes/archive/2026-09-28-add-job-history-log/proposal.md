## Why

A Backup Job or Restore Job today records only its final outcome (`Job.status`/`result_json`, plus
a best-effort, exception-swallowed single row in `backup_history`/`restore_history`). There is no
record of what happened during the run — no way to see that a backup spent ten minutes in
`SNAPSHOTING` before failing, or to distinguish "never started" from "failed partway through".
`SPEC.md` §17/§28 already describe Backup History and Restore History as append-only execution
logs; this change makes the existing tables actually behave that way, using the state information
StarRocks itself already reports during a poll (`executor.poll_backup_status` /
`restore.poll_restore_status`), rather than inventing a separate event vocabulary.

## What Changes

- **BREAKING**: `backup_history` and `restore_history` change from a single best-effort summary
  row keyed by `(cluster_id, label)` into an append-only log of state-change rows keyed by
  `job_id` (FK to `jobs.id`, `ON DELETE CASCADE`). Existing rows are not migrated forward (no
  `job_id` can be derived from `(cluster_id, label)` alone for historical rows); the migration
  drops and recreates both tables.
- Add a `history.append_backup_event(job_id, status, ...)` / `append_restore_event(...)` helper,
  each running its own short-lived session and only ever inserting. Each call is a no-op if
  `status` matches that job's most recently recorded row (dedup on state change); a terminal
  `SUCCESS`/`FAILED` row is always appended regardless of dedup.
- Wire `executor.poll_backup_status`'s existing `on_progress` callback, and
  `restore.poll_restore_status`'s equivalent, into these helpers, so every distinct StarRocks-
  native state (`SHOW BACKUP`/`SHOW RESTORE`'s `State` column, e.g. `PENDING`, `SNAPSHOTING`,
  `UPLOADING`) is recorded, plus a final `SUCCESS`/`FAILED` row when the job completes. Remove the
  old best-effort single-row `history.log_backup`/`history.log_restore` writes they replace.
- Add `GET /job/{job_id}/history` returning that job's log (from `backup_history` or
  `restore_history`, chosen by the job's `job_type`) in time order.
- Retention Jobs get no log table; `Job.status` alone covers them (out of scope here — see
  `PLAN.md` §10).
- **BREAKING**: `backup_history` no longer serves as the restore-lineage catalog it was
  incidentally also used for (`restore.find_restore_pair`/`find_backup_repository`, called from
  `commands/restore.py`, looked up a backup's type/repository/completion time by label). `Job`
  gains two new columns, `label` and `repository`, populated when known (repository at job
  submission, label once `labels.determine_backup_label` computes it); those lookups now query
  `Job` instead. Backup jobs completed before this change ships have `label = NULL`, so restoring
  from one of their labels afterward raises `BackupLabelNotFoundError` — accepted for the same
  reason as the `backup_history`/`restore_history` drop above.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `api-job-execution`: add a new requirement — a job's execution history (backup or restore) can
  be retrieved via `GET /job/{job_id}/history`, returned in time order.

## Impact

- `src/starrocks_br/store/models.py`: `BackupHistory`/`RestoreHistory` column changes; `Job` gains
  `label`/`repository` columns.
- New Alembic migration on top of `b7bf2d5f2365_baseline_schema`.
- `src/starrocks_br/history.py`: replace `log_backup`/`log_restore` with the new append-only
  helpers.
- `src/starrocks_br/executor.py`, `src/starrocks_br/restore.py`: wire `on_progress` into the new
  helpers instead of (or in addition to, until the old call sites are removed) their current
  best-effort final write; set `Job.label` once determined.
- `src/starrocks_br/commands/jobs.py`: `submit_job` sets `Job.repository` from `params` at
  creation time.
- `src/starrocks_br/restore.py`: `find_restore_pair`/`find_backup_repository` query `Job` instead
  of `backup_history`.
- `src/starrocks_br/api/routes/jobs.py`, `src/starrocks_br/api/schemas.py`: new
  `GET /job/{job_id}/history` route and response schema.
- `SPEC.md` §17/§28 already updated (this conversation) to describe StarRocks-native states with
  a final `SUCCESS`/`FAILED` row, ahead of this change's implementation.
