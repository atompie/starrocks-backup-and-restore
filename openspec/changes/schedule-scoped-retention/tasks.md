## 1. Schema, Models, and Retention DAL

- [ ] 1.1 Add `RetentionHistory` model in `store/models.py` and an Alembic migration creating `retention_history`; verify upgrade and downgrade against SQLite and PostgreSQL/MySQL schemas.
- [ ] 1.2 Add `append_retention_event` in `dal/metadata/history.py` and register `RetentionHistory` in `dal/metadata/jobs.py` under `_HISTORY_MODEL_BY_JOB_TYPE`; verify unit tests for appending and retrieval via `list_history_for_job`.
- [ ] 1.3 Implement retention DAL queries in `dal/metadata/retention.py` (eligible full backups per schedule, active incremental baseline ids, restore-source ids for `PENDING`/`RUNNING` restores, `mark_references_deleted`); verify unit tests covering candidate selection, baseline protection and restore protection; confirm `finished_at` is set on every `SUCCESS` job the ordering relies on.
- [ ] 1.4 Add `JobType.RETENTION` in `store/models.py`, and add `STARROCKS_BR_RETENTION_MAX_SECONDS` (default 1800) to `runtime_config.py` with validation; verify unit tests.

## 2. Retention Command

- [ ] 2.1 Implement `commands/retention.py::run_retention`: log `RETENTION_STARTED`, select droppable backups, per backup re-check protections and the deadline, drop snapshots via StarRocks (already-absent counts as dropped), log `SNAPSHOT_DROPPED`, set `deleted_at` in a short session, log `RETENTION_FINISHED` (with dropped ids and deadline flag); take no `RunStatus` slot and hold no session across StarRocks calls; verify service tests including the deadline stop and idempotent retry.
- [ ] 2.2 Map `"retention": run_retention` in `jobs/handlers.py`; verify handler dispatch.
- [ ] 2.3 Filter `deleted_at IS NULL` on references in `dal/metadata/backup_catalog.py` (`find_latest_full_backup_job`) and `dal/metadata/restore_catalog.py` (`find_latest_full_backup_before`, `list_partitions_for_label`); verify unit tests confirm deleted backups are not chosen as baselines or restore targets.

## 3. Tick Sweep

- [ ] 3.1 Implement `commands/retention.py::submit_due_retention_jobs`: for each recurring full-backup schedule that is not pending deletion, submit a `retention` job through `submit_job` when it has droppable candidates and no `PENDING`/`RUNNING` retention job; verify service tests (no job when only protected backups remain, no duplicate while one is open).
- [ ] 3.2 Call the sweep in `execute_scheduler_tick` after due-schedule submission and expiry and before dispatch; record submitted ids in `TickResult`; verify a tick submits and, on an idle cluster, admits the retention job.
- [ ] 3.3 Extend stale-job failure handling in `commands/jobs.py` so a failed `retention` job gets a `FAILED` retention history row; verify unit tests.

## 4. Retire Manual Prune Route

- [ ] 4.1 Remove `/backup/manual/prune/cluster/{cluster_id}` from `api/routes/jobs.py`, `PruneRequest` from `api/schemas.py`, and `commands/prune.py` and `dal/metadata/prune.py`; verify the retired route returns HTTP 404.
- [ ] 4.2 Clean up prune-specific unit tests and migrate suites relying on manual prune to schedule retention; verify unit tests pass.

## 5. Comprehensive Verification

- [ ] 5.1 Add service tests covering the `SPEC.md` §22 scenario (8 full jobs with failures, newest N kept, oldest non-baseline dropped, incremental baseline preserved); verify all tests pass.
- [ ] 5.2 Add service tests for independent schedule pools (`SPEC.md` §11: two schedules on the same inventory group and cluster keep independent counts); verify all tests pass.
- [ ] 5.3 Add ordering tests with the dispatcher: a retention job waits `PENDING` while a backup or restore is `RUNNING`, a backup submitted during a running retention waits and does not fail, and a restore queued for a candidate backup protects it; verify tests pass.
- [ ] 5.4 Add a failure-isolation test: a `DROP SNAPSHOT` error marks the retention job `FAILED` and leaves every backup job `SUCCESS`, and a later sweep retries the undeleted backup; verify test passes.
