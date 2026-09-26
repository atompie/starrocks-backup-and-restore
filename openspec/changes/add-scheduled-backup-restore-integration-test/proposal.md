## Why

The live integration suite covers a manually-triggered full backup and restore
(`tests/integration/test_full_backup_restore_cycle.py`), but nothing exercises the
scheduling path end to end: creating a schedule, having the run-due trigger actually
submit a job, and restoring from the backup that job produced. `api-scheduling`'s
"run-due" behavior is only covered by mocked service-level tests today, so a real
StarRocks/S3-backed regression could pass unit tests while the schedule-to-backup-to-restore
path is broken in practice.

## What Changes

- Add `tests/integration/test_scheduled_backup_restore.py`: register a cluster/repository/
  inventory group, create a schedule, force it due by backdating `Schedule.next_run_at`
  directly against the metadata store, call `POST /backup/schedules/run`, wait for the
  triggered job to succeed, look up the backup label it produced from the metadata store
  (`BackupHistory`/`Job.result_json` — the API surface doesn't expose it), drop and
  recreate the database, restore from that label via the existing manual restore endpoint,
  and assert the original rows are back.
- Refactor `tests/integration/test_full_backup_restore_cycle.py`: move its duplicated
  helpers (`it_names`, `seeded_database`, `_table_ddl`, `_wait_for_job`) into
  `tests/integration/conftest.py` so both tests share them.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
(none — this only adds test coverage for existing, already-specified behavior in
`api-scheduling` and `api-job-execution`; no requirement changes)

## Impact

- `tests/integration/test_scheduled_backup_restore.py` (new)
- `tests/integration/test_full_backup_restore_cycle.py` (helpers extracted, no behavior change)
- `tests/integration/conftest.py` (shared fixtures added)
- No production code changes.
