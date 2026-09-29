## Context

See `proposal.md` for motivation and `specs/api-scheduling/spec.md` for the observable deletion and expiry contract. Schedule deletion currently removes one metadata row synchronously. Backup references are per table/partition, while a StarRocks snapshot is identified by repository and snapshot label. Restore jobs currently keep their target backup label in `params_json`; there is no FK from a restore job to its source backup.

The existing due-schedule path uses a conditional update to make each due occurrence idempotent. Job creation commits the metadata row before enqueuing it, so the schedule deletion marker and cleanup job can be committed together before dispatch.

## Goals / Non-Goals

**Goals:**

- Make accepted schedule deletion durable, idempotent, and safe against a concurrent scheduler tick.
- Run StarRocks snapshot drops outside metadata transactions and make retries safe after partial completion.
- Preserve cleanup job status after the Schedule row is deleted.
- Store the restore source relationship so deleting a backup can remove the Restore Job and its history, including when source and target clusters differ.

**Non-Goals:**

- Change cluster deletion; that remains its later planned lifecycle.
- Change retention policy or the backup concurrency scope.
- Add a new scheduler process or dependency; expiry is called from the current scheduler command path.

## Decisions

### Mark accepted schedules as pending deletion

Add a nullable `deletion_requested_at` timestamp to Schedule. Schedule deletion validates and marks the row, then creates a `schedule_cleanup` job in one metadata transaction. A repeated request returns the existing PENDING/RUNNING cleanup job; a failed cleanup can be retried by creating a new job while retaining the marker. Successful cleanup deletes the Schedule. Once a schedule has `deletion_requested_at` set, any attempt to update it via PATCH returns HTTP 409.

`due_schedules` and its conditional advance must both require `deletion_requested_at IS NULL`. The conditional update is the serialization point: if due dispatch commits first, deletion sees the submitted backup job and returns 409; if deletion commits first, dispatch cannot advance the row. This was chosen over overloading `enabled`, which represents an operator-controlled recurring-schedule setting and is not an adequate deletion state. `ScheduleRead` surfaces `deletion_requested_at` so callers can inspect pending-deletion status.

### Keep cleanup jobs after their schedules

Set the cleanup Job's `schedule_id` while it runs so repeated DELETE can find it. Exclude `schedule_cleanup` itself from the schedule's backup-job deletion set. The existing `ON DELETE SET NULL` relationship clears the cleanup job's schedule id when the Schedule is deleted, preserving its pollable terminal record. Update `Schedule.last_run_job_id` with `ondelete="SET NULL"` in the migration and explicitly clear `Schedule.last_run_job_id` before deleting schedule backup jobs to ensure portability across database engines.

### Use a durable restore-to-backup relationship

Add nullable `Job.source_backup_job_id`, a self-referencing FK with `ON DELETE CASCADE`, and populate it for restore jobs when their source backup is resolved. Expose `source_backup_job_id` on `JobRead` matching `baseline_job_id`. Query PENDING/RUNNING restores through this field for the synchronous 409 guard. Restore submission must atomically check that the source Schedule is not pending deletion and create the Restore Job: if submission commits first, deletion sees the active restore; if deletion commits first, submission sees the marker and returns 409. Deleting a source backup then cascades its dependent Restore Job and restore history. Backfill existing rows by matching a restore's `target_label` and cluster against successful backup labels; leave unmatched legacy jobs without a source link.

This is preferred to repeatedly parsing `params_json`: a foreign key makes the dependency query explicit, supports cross-cluster restore lineage, and enforces the cleanup cascade in the metadata store.

### Drop snapshots outside metadata transactions

The cleanup handler reads the schedule's backup references into a short-lived session, closes it, and deduplicates by `(repository, snapshot_label)`. If a schedule has no references (e.g. failed backup jobs), snapshot dropping is a no-op. It connects to StarRocks and drops each snapshot without holding a metadata transaction. An already-absent snapshot (verified via `SHOW SNAPSHOT` or error inspection) counts as complete; other StarRocks errors fail the cleanup job and leave the Schedule and metadata intact. After all drops succeed, a new short transaction clears `last_run_job_id`, deletes the schedule's backup jobs (allowing FK cascades for their histories, references, and dependent restores), and deletes the Schedule. The cleanup Job itself remains.

### Route retirement, test migration, and API response

Remove only the manual full and incremental backup routes. Keep manual restore and prune routes unchanged. Migrate existing test suites (`test_api_jobs.py`, `test_api_clusters.py`, `test_full_backup_restore_cycle.py`, `test_backup_dispatch_parity.py`) from manual backup routes to one-shot schedules (`cadence=None`) or direct job submission. Schedule DELETE returns the cleanup Job using `JobRead` with HTTP 202.

Provide `commands.schedules.expire_due_schedules(db, now)` as the shared command entry point for one-shot schedule expiry. The existing scheduler tick (`run_due_schedules` / `POST /backup/schedules/run`) invokes it, including triggered `schedule_cleanup` job IDs in `RunDueResponse`, preparing the exact function needed by Section 9's CLI tick.

## Risks / Trade-offs

- **A process can stop after snapshot drops but before metadata deletion** → treat absent snapshots as already complete so the next cleanup attempt can safely finish.
- **A source-backup migration may not match every legacy Restore Job** → backfill only unambiguous successful backup matches and retain unmatched history; new restore submissions always record the FK.
- **Concurrent delete and due-dispatch transactions can contend** → use a conditional metadata update including the deletion marker and keep validation, marker, and cleanup-job creation in short transactions.
- **Clients relying on manual backup endpoints or `204` schedule deletion break** → document the route migration to one-shot/recurring schedules and return the cleanup job id for polling.
- **`add-cli-scheduler-command`'s existing `api-scheduling` delta scenario said a one-shot schedule is "never modified" by due-schedule execution, which read as contradicting this change's expiry cleanup** → corrected in that change's spec delta to scope the exclusion to recurring backup submission only, distinct from `expire_due_schedules`; verify on sync that the merged main `api-scheduling/spec.md` keeps both scenarios non-contradictory.

## Migration Plan

1. Add the schedule deletion marker and restore source-backup FK, with a migration backfill for resolvable existing restore jobs.
2. Deploy code that writes the restore FK for new Restore Jobs and excludes pending-deletion schedules from due dispatch.
3. Deploy the cleanup handler and update schedule DELETE to validate, mark, create the job, and return `202`.
4. Add expiry submission to the scheduler tick, then remove the two manual backup routes.

Rollback after accepting cleanup jobs is unsafe if some snapshots have already been dropped. Roll back code only after pending cleanup jobs have reached terminal states; schema rollback is not required to restore already-cleaned data.
