## 1. Persist cleanup state and restore lineage

- [ ] 1.1 Add `Schedule.deletion_requested_at` and `Job.source_backup_job_id`, alter `Schedule.last_run_job_id` FK to `ondelete="SET NULL"`, expose `source_backup_job_id` on `JobRead` and `deletion_requested_at` on `ScheduleRead`, with a migration that backfills unambiguous existing restore sources; verify migration upgrade and downgrade against the supported metadata database.
- [ ] 1.2 Add DAL queries for active schedule backups, dependent restores, incremental baseline checks, cleanup references, and schedule-owned job deletion; verify cascade ordering clears `Schedule.last_run_job_id` and preserves the cleanup Job.

## 2. Coordinate deletion, dispatch, and restore submission

- [ ] 2.1 Implement schedule deletion validation and atomically mark accepted schedules pending deletion while creating a `schedule_cleanup` job; block `PATCH` on pending-deletion schedules with 409; verify conflicts return 409 and repeated requests reuse the active cleanup job.
- [ ] 2.2 Exclude pending-deletion schedules in both due-schedule selection and its conditional advance; verify concurrent deletion and due dispatch cannot submit a backup after deletion is accepted.
- [ ] 2.3 Resolve and persist the source backup for each Restore Job, reject restore submission with 409 when its source schedule is pending deletion, and verify deletion sees active restores submitted first.

## 3. Implement schedule cleanup jobs

- [ ] 3.1 Register `schedule_cleanup` in `JobType` and `JOB_HANDLERS`, implement a handler in `commands/schedules.py` that loads distinct `(repository, snapshot_label)` references, drops snapshots without an open metadata transaction, treats already-absent snapshots as complete (handling empty reference sets), and records terminal job status; verify partial-failure retry behavior.
- [ ] 3.2 After all snapshot drops succeed, clear the schedule's last-run pointer, delete its backup Jobs and dependent Restore Jobs through FK cascades, then delete the Schedule while preserving the cleanup Job; verify all related histories and references are removed.

## 4. Update API, test suites, and expiry processing

- [ ] 4.1 Remove only `/backup/manual/full` and `/backup/manual/incremental`, leaving manual restore and prune routes intact; verify retired routes return 404 and one-shot schedule creation still starts a full backup.
- [ ] 4.2 Migrate existing test callers (`test_api_jobs.py`, `test_api_clusters.py`, `test_full_backup_restore_cycle.py`, `test_backup_dispatch_parity.py`) from manual backup routes to one-shot schedules (`cadence=None`) or direct job submission; verify existing tests pass against the retired routes.
- [ ] 4.3 Change schedule DELETE to return HTTP 202 with `JobRead`, map active backup/restore and baseline conflicts to 409, and preserve 404 cluster scoping; verify API responses and job polling.
- [ ] 4.4 Implement `commands.schedules.expire_due_schedules(db, now)` and invoke it from the scheduler tick (`run_due_schedules` / `POST /backup/schedules/run`), including cleanup job IDs in `RunDueResponse`; verify blocked expiries are logged and retried, failed cleanup is retried, and schedules without expiry remain.

## 5. Verify end-to-end cleanup

- [ ] 5.1 Add unit service coverage for accepted deletion, active-job and restore conflicts, baseline conflicts, duplicate requests, partial cleanup retry, patch guard on pending schedules, and expiry behavior; verify the focused unit test suite passes.
- [ ] 5.2 Add an integration test against real StarRocks and object storage that creates a one-shot backup, cleans up its schedule, and confirms the snapshot and metadata records are removed; verify it skips cleanly when external infrastructure is unavailable.
