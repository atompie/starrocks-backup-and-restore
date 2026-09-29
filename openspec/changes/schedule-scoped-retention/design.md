## Context

See `proposal.md` for motivation and `exploration.md` for the path taken. Today retention is only reachable through the legacy manual route `POST /backup/manual/prune/cluster/{cluster_id}`, which groups backups across all schedules by inventory group id and deletes `Job` rows directly via `prune.cleanup_backup_history`.

Under the domain specification (`SPEC.md` §2, §10, §11, §21-23), retention must be an automatic, schedule-scoped operation managed by an independent `retention` job. `BackupReference.deleted_at` (added in Section 6) is waiting for retention to populate it.

`cluster-job-dispatcher` makes the scheduler tick the only thing that starts jobs, admitting at most one job per cluster at a time (restore, then backup, then other work). Retention builds on that guarantee.

## Goals / Non-Goals

**Goals:**
- Keep each recurring full-backup schedule's pool at `schedule.retention` successful full backups, automatically.
- Preserve full backups that serve as incremental baselines or as sources of active restores.
- Soft-delete data (`DROP SNAPSHOT` per reference and set `BackupReference.deleted_at = now`) while keeping `Job` and `backup_history` rows permanently intact.
- Record append-only retention history in a dedicated `retention_history` table.
- Never affect any backup's status; never fail because the cluster is busy.
- Retire the legacy `POST /backup/manual/prune/cluster/{cluster_id}` endpoint.

**Non-Goals:**
- One-shot schedules: they have `cadence = null` and are cleaned up via `expire_due_schedules` / `schedule_cleanup` (Section 8).
- Incremental backups: they have no retention and are never dropped by retention (Decision 0.5).
- Altering restore execution beyond ensuring pruned backups cannot be selected as restore targets or baselines.
- Any concurrency-slot logic for retention; ordering is the dispatcher's job.

## Decisions

### 1. Trigger: a tick sweep, not a post-backup hook

`commands/retention.py::submit_due_retention_jobs(session)` runs in `execute_scheduler_tick` after `run_due_schedules`/`expire_due_schedules` and before dispatch. For each recurring full-backup schedule (`job_type == 'backup_full'`, `cadence IS NOT NULL`, not pending deletion) it computes the schedule's droppable candidates (Decision 2, including protections). If there is at least one and the schedule has no `PENDING`/`RUNNING` retention job, it submits a `retention` job (`schedule_id` set, params `{schedule_id}`) through `submit_job`.

*Rationale*: the dispatcher removes any need to react to a specific backup finishing. A sweep needs no coupling to the execution backend, survives restarts, is self-healing (a job that stops at its deadline or a run that was skipped is re-created next tick), and counts only *droppable* candidates so protected old backups do not create a job every tick. Because the backup is already `SUCCESS` in the database when a tick runs, it is reliably counted.

*Alternatives considered*:
- A `finish_job_success` hook in each backend (earlier design): rejected; it duplicated the trigger per backend and lost the trigger on any failure.
- Running retention inline in `run_backup_full`: rejected; `SPEC.md` §2/§23 require an observable Retention Job with its own id, status and history, and failure isolation.

### 2. Selection query and protection in the DAL

`dal/metadata/retention.py` selects strictly for `schedule_id`:
1. Find `Job` records with `schedule_id == schedule.id`, `job_type == 'backup_full'`, `status == 'SUCCESS'`, and at least one `BackupReference` with `deleted_at IS NULL`.
2. Order by `finished_at.desc(), id.desc()`.
3. Keep the first `schedule.retention`; the rest are candidates.
4. Protect any candidate whose `id` is in the cluster-wide set of `baseline_job_id` values from `Job` where `job_type == 'backup_incremental'` and `status IN ('PENDING','RUNNING','SUCCESS')`.
5. Protect any candidate that is the `source_backup_job_id`, or the `baseline_job_id` of the source, of a restore `Job` with `status IN ('PENDING','RUNNING')` on the cluster.
6. The remaining candidates are droppable. The sweep and `run_retention` both use this query; `run_retention` re-evaluates protections immediately before each backup's drop.

*Rationale*: satisfies §11 (independent pools) and §21/Decision Q1 (baseline protection). Restore protection matters because a queued restore can wait behind a running retention job; checking again per drop closes that window. `finished_at` is set for every `SUCCESS` job (verify in tasks).

### 3. Handler: no slot, per-backup commits, idempotent drops, deadline

`commands/retention.py::run_retention(cluster, params, job_id, on_progress)` is registered in `JOB_HANDLERS`. It does not reserve a `RunStatus` slot: the dispatcher guarantees no other job is running on the cluster. It opens no metadata session across StarRocks calls. For each droppable backup, in order:
1. If `STARROCKS_BR_RETENTION_MAX_SECONDS` (default 1800) has elapsed since the handler started, stop starting new drops.
2. Re-check protections (Decision 2, step 4-5) for this backup.
3. Fetch distinct `(repository, snapshot_label)` from its `BackupReference` rows with `deleted_at IS NULL`.
4. Execute `DROP SNAPSHOT ON {repository} WHERE SNAPSHOT = {snapshot_label}` per snapshot; an already-absent snapshot counts as dropped.
5. Append `SNAPSHOT_DROPPED` to `retention_history`.
6. In a short session set `BackupReference.deleted_at = now` for that backup.

At the end it appends `RETENTION_FINISHED` (details include dropped ids and whether the deadline was reached). A deadline stop is a normal `SUCCESS`; the next sweep sees the remaining droppable backups and submits a new job.

*Rationale*: committing `deleted_at` per backup keeps metadata equal to physical reality if a later drop fails, and idempotent drops make retries safe. The deadline bounds how long the lowest-priority job can hold a cluster's lane against later backups.

### 4. Dedicated `RetentionHistory` table

`retention_history` (`RetentionHistory` model) matches `BackupHistory`/`RestoreHistory`: `id`, `job_id` (FK `jobs.id` `ON DELETE CASCADE`), `ts`, `status`, `message`, `details_json`. Events: `RETENTION_STARTED`, `SNAPSHOT_DROPPED`, `RETENTION_FINISHED`, `ERROR`, `FAILED`. `_HISTORY_MODEL_BY_JOB_TYPE["retention"] = RetentionHistory` in `dal/metadata/jobs.py` so `GET /job/{id}/history` works. A retention job that ends `FAILED` always has a `FAILED` entry with the error; stale-job reconciliation must write it for `retention` jobs as it does for backup/restore.

### 5. Lineage lookup filtering on `deleted_at IS NULL`

Add `BackupReference.deleted_at.is_(None)` to `find_latest_full_backup_job` (`backup_catalog.py`), `find_latest_full_backup_before` and `list_partitions_for_label` (`restore_catalog.py`), so deleted backups cannot be resolved as baselines or restore targets (`SPEC.md` §14, §26).

### 6. Retirement of the manual prune route

Remove `POST /backup/manual/prune/cluster/{cluster_id}`, `PruneRequest`, `commands/prune.py` and `dal/metadata/prune.py`. Keep the `JobType.PRUNE` enum entry so historical jobs in existing databases still read.

## Risks / Trade-offs

- **[Risk]** Retention starves on a cluster that is always busy with backups/restores.
  - **Mitigation**: accepted; it is cleanup, and the sweep keeps re-creating the job until it runs.
- **[Risk]** A very large backlog exceeds the deadline.
  - **Mitigation**: each run makes progress and is resumed next tick.
- **[Risk]** StarRocks/S3 outage during `DROP SNAPSHOT`.
  - **Mitigation**: per-backup commits; the failed backup keeps `deleted_at IS NULL` and is retried by a later sweep; the retention job records `FAILED`.
- **[Risk]** A restore is submitted for a backup between candidate selection and its drop.
  - **Mitigation**: protections are re-checked immediately before each drop.
- **[Risk]** Breaking clients that call `POST /backup/manual/prune/...`.
  - **Mitigation**: documented as **BREAKING**; clients manage retention declaratively via `Schedule.retention`.
- **[Dependency]** Requires `cluster-job-dispatcher`; without it retention would have no serialization.

## Migration Plan

1. **Alembic migration**: add the `retention_history` table.
2. **Models and DAL**: `RetentionHistory`, `append_retention_event` in `history.py`, registration in `jobs.py`, `dal/metadata/retention.py`.
3. **Lineage queries**: add `deleted_at.is_(None)` filters to `backup_catalog.py` and `restore_catalog.py`.
4. **Retention command**: `commands/retention.py` (`run_retention`, `submit_due_retention_jobs`), register in `JOB_HANDLERS`, add `STARROCKS_BR_RETENTION_MAX_SECONDS` to `runtime_config.py`.
5. **Tick wiring**: call the sweep in `execute_scheduler_tick` before dispatch; extend stale-job failure handling to write a `FAILED` retention history row.
6. **Retire prune**: remove routes, schema and old prune modules.
7. **Rollback**: Alembic downgrade drops `retention_history`; revert route removals if needed.
