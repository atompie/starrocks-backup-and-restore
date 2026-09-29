## 1. Schema, Models, and Retention DAL

- [ ] 1.1 Add `RetentionHistory` model in `store/models.py` and create an Alembic migration creating the `retention_history` table; verify migration upgrade and downgrade against SQLite and PostgreSQL/MySQL schemas.
- [ ] 1.2 Add `append_retention_event` in `dal/metadata/history.py` and register `RetentionHistory` in `dal/metadata/jobs.py` under `_HISTORY_MODEL_BY_JOB_TYPE`; verify unit tests for history appending and retrieval via `list_history_for_job`.
- [ ] 1.3 Implement retention DAL queries in `dal/metadata/retention.py` (`get_eligible_full_backup_jobs`, `get_active_incremental_baseline_ids`, `mark_references_deleted`); verify unit tests against mock/SQLite sessions covering candidate selection and baseline protection.

## 2. Retention Command & Concurrency

- [ ] 2.1 Implement `commands/retention.py::run_retention` that reserves the `backup` concurrency slot, logs `RETENTION_STARTED`, selects eligible candidates, drops snapshots via StarRocks (treating already-absent snapshots as complete), logs `SNAPSHOT_DROPPED`, updates `BackupReference.deleted_at` immediately per job, logs `RETENTION_FINISHED`, and releases the slot in `finally`; verify unit tests.
- [ ] 2.2 Register `JobType.RETENTION` in `store/models.py` and map `"retention": run_retention` in `jobs/handlers.py`; verify job handler dispatch.
- [ ] 2.3 Filter `deleted_at IS NULL` on references in `dal/metadata/backup_catalog.py` (`find_latest_full_backup_job`) and `dal/metadata/restore_catalog.py` (`find_latest_full_backup_before`, `list_partitions_for_label`); verify unit tests confirm deleted backups are not picked as baselines or restore targets.

## 3. Post-Job Success Trigger Hook

- [ ] 3.1 Implement `commands.jobs.finish_job_success` which marks the completed job `SUCCESS` and, if it is a recurring full backup (`cadence is not None`), submits an asynchronous `retention` job for its schedule; verify that failure during retention submission is isolated and leaves the backup job in `SUCCESS`.
- [ ] 3.2 Wire `finish_job_success` into `jobs/thread_backend.py` (replacing the direct `jobs_dal.mark_success` call); verify unit test confirms retention job submission following successful full-backup execution.

## 4. Retire Manual Prune Route

- [ ] 4.1 Remove `/backup/manual/prune/cluster/{cluster_id}` route from `api/routes/jobs.py`, remove `PruneRequest` from `api/schemas.py`, and remove `commands/prune.py` and `dal/metadata/prune.py`; verify calling the retired route returns HTTP 404.
- [ ] 4.2 Clean up prune-specific unit tests and migrate any test suites relying on manual prune to schedule retention; verify unit tests pass.

## 5. Comprehensive Verification

- [ ] 5.1 Add service tests covering the complete `SPEC.md` §22 scenario (8 full jobs with failures, newest N kept, oldest non-baseline dropped, incremental baseline preserved); verify all tests pass.
- [ ] 5.2 Add service tests for independent schedule pools (`SPEC.md` §11: two schedules on the same inventory group and cluster maintain independent retention counts); verify all tests pass.
- [ ] 5.3 Add concurrency test verifying a retention job blocks on an active backup slot and cannot overlap with another backup on the same cluster; verify test passes.
