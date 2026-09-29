## Context

See `proposal.md` for motivation and requirements. Today, retention is only accessible through the legacy manual route `POST /backup/manual/prune/cluster/{cluster_id}`, which groups backups across all schedules by inventory group id and deletes `Job` rows directly via `prune.cleanup_backup_history`.

Under the domain specification (`SPEC.md` §2, §10, §11, §21-23), retention must be an automatic, schedule-scoped operation managed by an independent `retention` job. Full backups serving as incremental baselines (Decision Q1, §21) must be preserved indefinitely. Backup references (`BackupReference`) already have a nullable `deleted_at` column added in Section 6 (`backup-references`), waiting for retention to populate it.

## Goals / Non-Goals

**Goals:**
- Automatically dispatch a `retention` job when a recurring full backup succeeds.
- Enforce schedule-scoped retention (`schedule.retention` copies kept), preserving full backups that serve as incremental baselines across the cluster.
- Soft-delete data (`DROP SNAPSHOT` per reference and set `BackupReference.deleted_at = now`) while keeping `Job` and `backup_history` rows permanently intact.
- Record append-only retention execution history in a dedicated `retention_history` table.
- Guarantee concurrency serialization under `scope="backup"`, while ensuring retention failures never fail or alter the triggering backup job.
- Retire the legacy `POST /backup/manual/prune/cluster/{cluster_id}` endpoint and prune job type.

**Non-Goals:**
- Not modifying one-shot schedules: one-shot schedules have `cadence = null` and are cleaned up via `expire_due_schedules` / `schedule_cleanup` (Section 8).
- Not applying retention to incremental backups: incremental backups have no retention and are never dropped by retention (Decision 0.5).
- Not altering restore execution: restore consumption of `deleted_at` is limited to ensuring pruned backups cannot be selected as restore targets or incremental baselines.

## Decisions

### 1. Trigger retention via post-job success hook

Retention is submitted from a centralized `commands.jobs.finish_job_success(job_id, result_json)` hook invoked by execution backends (e.g. `thread_backend.py:48`) immediately after marking `Job.status = 'SUCCESS'`.

*Rationale*: If retention were submitted inside `commands.backup.run_backup_full` before returning, the backup job would still be in `RUNNING` status in the database when the retention worker starts, causing candidate queries to miss it. By triggering retention after `mark_success` commits, the triggering backup is reliably counted among the retained copies. Furthermore, if submitting or executing retention fails, the backup job's `SUCCESS` status is already durable and unaffected.

*Alternatives considered*:
- Submitting retention synchronously inside `run_backup_full`: Rejected because of the race condition with metadata status commit and violation of failure isolation.
- Running retention in-process inside `run_backup_full` without a job: Rejected because `SPEC.md` §2 and §23 define Retention Job as an explicit, observable domain entity with its own ID, status, and history.

### 2. Selection query and baseline protection in DAL

The retention query in `dal/metadata/retention.py` selects candidates strictly for `schedule_id`:
1. Find all `Job` records where `schedule_id == schedule.id`, `job_type == "backup_full"`, `status == "SUCCESS"`, and at least one `BackupReference` has `deleted_at IS NULL`.
2. Order by `finished_at.desc(), id.desc()`.
3. The first `schedule.retention` jobs are retained.
4. Jobs beyond `retention` are candidate drops.
5. Query cluster-wide incremental baselines: find all distinct `baseline_job_id` values from `Job` where `cluster_id == cluster.id`, `job_type == "backup_incremental"`, and `status IN ('PENDING', 'RUNNING', 'SUCCESS')`.
6. Any candidate job whose `id` is in the active baseline set is skipped and kept.
7. The remaining candidates are marked for snapshot deletion.

*Rationale*: This satisfies `SPEC.md` §11 (independent pools per schedule) and §21 / Decision Q1 (cluster-wide baseline protection). Checking `PENDING` and `RUNNING` incrementals guarantees that in-flight incrementals do not lose their baseline mid-execution.

### 3. Immediate per-job metadata commits and idempotent drops

The retention execution loop in `commands/retention.py` connects to StarRocks outside metadata sessions. For each job to drop:
1. Fetch distinct `(repository, snapshot_label)` from `BackupReference` where `job_id == job.id` and `deleted_at IS NULL`.
2. For each snapshot, execute `DROP SNAPSHOT ON {repository} WHERE SNAPSHOT = {snapshot_label}`. An already-absent snapshot in StarRocks is treated as successfully dropped.
3. Append a `SNAPSHOT_DROPPED` event to `RetentionHistory`.
4. In a short metadata session, immediately update `BackupReference.deleted_at = now` for that job.

*Rationale*: Committing `deleted_at` per job immediately ensures metadata matches physical reality in S3/StarRocks. If dropping snapshot 2 of 3 fails, snapshot 1 is already marked deleted and will not be re-dropped on retry. Treating absent snapshots as successful makes retries completely idempotent.

### 4. Dedicated `RetentionHistory` model and table

Add `retention_history` table (`RetentionHistory` model) matching `BackupHistory` and `RestoreHistory`:
- Columns: `id`, `job_id` (FK to `jobs.id` `ON DELETE CASCADE`), `ts`, `status`, `message`, `details_json`.
- Event types: `RETENTION_STARTED`, `SNAPSHOT_DROPPED`, `RETENTION_FINISHED`, `ERROR`, `FAILED`.
- Map `_HISTORY_MODEL_BY_JOB_TYPE["retention"] = RetentionHistory` in `dal/metadata/jobs.py` so `GET /job/{id}/history` works automatically.

*Rationale*: Re-establishes alignment with `SPEC.md` §2 ("Retention Job └── Retention History (1:N)") and §23, giving operators auditable visibility into snapshot pruning actions.

### 5. Lineage lookup filtering on `deleted_at IS NULL`

Update `find_latest_full_backup_job` (`backup_catalog.py`), `find_latest_full_backup_before` (`restore_catalog.py`), and `list_partitions_for_label` (`restore_catalog.py`) to add `BackupReference.deleted_at.is_(None)`.

*Rationale*: Ensures deleted backups cannot be resolved as baselines for new incrementals or selected as restore targets, fulfilling `SPEC.md` §14 and §26.

### 6. Retirement of manual prune route

Remove `POST /backup/manual/prune/cluster/{cluster_id}` and `PruneRequest`. Remove `commands/prune.py` and `dal/metadata/prune.py`. Keep `JobType.PRUNE` enum entry for backwards-compatible reading of historical jobs in existing databases.

*Rationale*: Eliminates the conflicting inventory-scoped prune mechanism, consolidating all retention management under schedule-scoped policies.

## Risks / Trade-offs

- **[Risk]** A retention job fails to acquire `scope="backup"` because another backup is running on the cluster.
  - **Mitigation**: The retention job fails with `ConcurrencyConflictError` and records `FAILED` in its history. The original backup remains `SUCCESS`. On the next recurring full backup, the selection query automatically re-evaluates all un-pruned backups (`deleted_at IS NULL`) and drops them.
- **[Risk]** StarRocks/S3 outage during `DROP SNAPSHOT`.
  - **Mitigation**: Dropping is executed per job; completed jobs are committed immediately. Failed jobs remain with `deleted_at IS NULL` and are retried on the next run.
- **[Risk]** Breaking existing clients calling `POST /backup/manual/prune/...`.
  - **Mitigation**: Documented as **BREAKING** in `proposal.md` and `specs/api-job-execution/spec.md`. Clients manage retention declaratively via `Schedule.retention`.

## Migration Plan

1. **Alembic migration**: Add `retention_history` table (`job_id`, `ts`, `status`, `message`, `details_json`).
2. **Core DAL & Models**: Add `RetentionHistory` model, implement `dal/metadata/retention.py`, add `append_retention_event` in `history.py`, and register retention history in `jobs.py`.
3. **Lineage queries**: Add `deleted_at.is_(None)` filters to `backup_catalog.py` and `restore_catalog.py`.
4. **Retention command**: Implement `commands/retention.py::run_retention` and register in `JOB_HANDLERS`.
5. **Backend hook**: Implement `commands.jobs.finish_job_success` and update `jobs/thread_backend.py` to invoke it.
6. **Retire prune**: Remove prune routes, schema, and old prune modules.
7. **Rollback strategy**: Standard Alembic downgrade to drop `retention_history` table; revert route removals if needed.
