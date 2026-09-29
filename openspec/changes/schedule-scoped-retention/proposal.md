## Why

In `SPEC.md` (§10, §11, §21-23) and `PLAN.md` (§10), retention is defined as an automated, schedule-scoped operation that manages a schedule's pool of retained backups, rather than a manual inventory-wide prune that deletes job metadata. Today's legacy manual prune route (`POST /backup/manual/prune/cluster/{cluster_id}`) violates schedule independence (§11) by operating across schedules by inventory group, and violates job history immutability (§13-14) by deleting `Job` rows and historical execution logs.

Implementing schedule-scoped retention as an independent `retention` job enforces retention automatically after every recurring full backup, protects full backups that serve as incremental baselines, isolates backup job status from retention outcomes, and retires the conflicting manual prune route.

## What Changes

- **Add `retention` job type**: Register `JobType.RETENTION` as a tracked asynchronous job and peer domain entity (`SPEC.md` §2, §23).
- **Automated retention dispatch**: Upon successful completion of a recurring full backup, automatically submit a `retention` job scoped to that schedule.
- **Schedule-scoped selection & baseline protection**: The retention job evaluates successful full backups belonging exclusively to the schedule (`deleted_at IS NULL`), keeps the newest `retention` count, and protects any older candidate that is the baseline of an existing incremental backup (`status IN [PENDING, RUNNING, SUCCESS]`).
- **Data deletion & metadata preservation**: Dropping data executes `DROP SNAPSHOT` per reference against StarRocks/S3 and sets `BackupReference.deleted_at = now` immediately per job. The `Job` row and its `backup_history` remain permanently intact as historical records.
- **Dedicated retention history**: Record append-only events (`RETENTION_STARTED`, `SNAPSHOT_DROPPED`, `RETENTION_FINISHED`, `ERROR`, `FAILED`) in a dedicated `retention_history` table, exposed via `GET /job/{id}/history`.
- **Concurrency & failure isolation**: Retention reserves `scope="backup"`, serializing with other backup and retention operations on the cluster. Retention failures never change the triggering backup job's status, and un-pruned snapshots are retried automatically on the next run.
- **Retire manual prune route (**BREAKING**)**: Remove `POST /backup/manual/prune/cluster/{cluster_id}`, remove `PruneRequest`, and retire inventory-scoped pruning.

## Capabilities

### Modified Capabilities

- `api-job-execution`: Retires the manual prune endpoint `POST /backup/manual/prune/cluster/{cluster_id}` (**BREAKING**), removes prune request validation requirements, and adds `retention` job history retrieval support under `GET /job/{job_id}/history`.
- `api-scheduling`: Documents that successful execution of a recurring full-backup job automatically submits an asynchronous `retention` job to enforce the schedule's configured retention copy limit.

## Impact

- **API Changes**: `POST /backup/manual/prune/cluster/{cluster_id}` is retired and returns 404 (**BREAKING**). `GET /job/{id}/history` now returns `RetentionHistoryRead` entries for `job_type="retention"`.
- **Database Schema**: New migration adding `retention_history` table (`job_id`, `ts`, `status`, `message`, `details_json`).
- **Internal Core & DAL**: New DAL modules for retention queries (`dal/metadata/retention.py`, `dal/db/retention.py`), new command handler `commands/retention.py`, and job success trigger hook in the execution backend. Readers resolving restore lineage (`find_latest_full_backup_job`, `find_latest_full_backup_before`, `list_partitions_for_label`) filter out `deleted_at IS NOT NULL` references.
- **Tests**: Legacy prune tests migrated or removed; comprehensive service tests added for retention selection, baseline protection (§22 scenario), concurrency slot reservation, independent schedule pools (§11), and failure isolation.
