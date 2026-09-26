## 1. Extract shared fixtures

- [ ] 1.1 Move `it_names`, `seeded_database`, `_table_ddl`, and `_wait_for_job` from
      `tests/integration/test_full_backup_restore_cycle.py` into
      `tests/integration/conftest.py`, and verify `test_full_backup_restore_cycle.py`
      still passes unchanged against the local StarRocks/MinIO stack (or skips cleanly
      when it isn't running).

## 2. Metadata-store test helpers

- [ ] 2.1 Add a small helper (in `tests/integration/conftest.py` or the new test file)
      that opens a `Session` against `starrocks_br.store.session.get_engine()` to (a)
      backdate a `Schedule.next_run_at`, and (b) read the backup label a completed job
      produced from `BackupHistory`/`Job.result_json`. Verify by unit-level sanity: the
      helper runs inside a test and returns the expected types without raising.

## 3. Scheduled backup + restore integration test

- [ ] 3.1 Create `tests/integration/test_scheduled_backup_restore.py`: register a
      cluster, S3-backed repository, and inventory group (reusing the shared fixtures
      from task 1.1).
- [ ] 3.2 Create a schedule via `POST /backup/schedules/cluster/{cluster_id}` with
      `job_type="backup_full"`, the seeded group/repository, and a valid cadence.
- [ ] 3.3 Backdate the schedule's `next_run_at` using the task 2.1 helper, then call
      `POST /backup/schedules/run` and assert the response's `triggered_job_ids`
      contains exactly one job id.
- [ ] 3.4 Wait for the triggered job to reach `SUCCESS` via the existing
      `_wait_for_job` helper.
- [ ] 3.5 Look up the backup label the job produced using the task 2.1 helper.
- [ ] 3.6 Drop the seeded database, recreate it (empty database + table shell, matching
      the pattern in `test_full_backup_restore_cycle.py`), and assert the table has zero
      rows.
- [ ] 3.7 Restore via `POST /backup/manual/restore/cluster/{cluster_id}` using the
      label from 3.5, wait for that job to reach `SUCCESS`.
- [ ] 3.8 Assert the original seeded rows are present in the table after restore, and
      that repository/cluster cleanup in a `finally` block mirrors
      `test_full_backup_restore_cycle.py`.

## 4. Verification

- [ ] 4.1 Run `pytest tests/integration/test_scheduled_backup_restore.py
      tests/integration/test_full_backup_restore_cycle.py -v` against the local
      StarRocks/MinIO dev stack and confirm both pass.
- [ ] 4.2 Run the full test suite (`pytest`) and confirm no regressions from the
      fixture extraction in task 1.1.
