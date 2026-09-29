## Why

In `SPEC.md` (§10, §11, §21-23) and `PLAN.md` (§10), retention is defined as an automated, schedule-scoped operation that manages a schedule's pool of retained backups, rather than a manual inventory-wide prune that deletes job metadata. Today's legacy manual prune route (`POST /backup/manual/prune/cluster/{cluster_id}`) violates schedule independence (§11) by operating across schedules by inventory group, and violates job history immutability (§13-14) by deleting `Job` rows and historical execution logs.

Implementing schedule-scoped retention as an independent `retention` job keeps each schedule's pool at its configured size, protects full backups that serve as incremental baselines or restore sources, isolates backup job status from retention outcomes, and retires the conflicting manual prune route.

This change depends on `cluster-job-dispatcher`: retention is lowest-priority cleanup and relies on the tick's one-job-per-cluster lane instead of taking any concurrency slot of its own.

## What Changes

- **Add `retention` job type**: Register `JobType.RETENTION` as a tracked asynchronous job and peer domain entity (`SPEC.md` §2, §23).
- **Tick sweep creates retention jobs**: each scheduler tick, before dispatch, finds every recurring full-backup schedule whose pool holds droppable backups beyond its `retention` count and that has no `PENDING`/`RUNNING` retention job, and submits a `retention` job for it. There is no post-backup hook, so a failed or skipped run is simply picked up by a later tick.
- **Serialization by the dispatcher**: a retention job waits as `PENDING` behind restore and backup work on its cluster and never overlaps any other job there. It takes no `RunStatus` slot and cannot fail because the cluster is busy.
- **Schedule-scoped selection & protection**: evaluates successful full backups belonging exclusively to the schedule (`deleted_at IS NULL`), keeps the newest `retention` count, and protects any older candidate that is the baseline of an existing incremental backup (`status IN [PENDING, RUNNING, SUCCESS]`) or the source (or baseline of the source) of a `PENDING`/`RUNNING` restore.
- **Data deletion & metadata preservation**: `DROP SNAPSHOT` per reference against StarRocks/S3, then `BackupReference.deleted_at = now` immediately per backup. The `Job` row and its `backup_history` remain permanently intact.
- **Deadline**: the handler stops starting new drops once `STARROCKS_BR_RETENTION_MAX_SECONDS` has elapsed, ends normally, and the next sweep continues the remainder.
- **Dedicated retention history**: append-only events (`RETENTION_STARTED`, `SNAPSHOT_DROPPED`, `RETENTION_FINISHED`, `ERROR`, `FAILED`) in a `retention_history` table, exposed via `GET /job/{id}/history`.
- **Failure isolation**: retention never changes any backup job's status.
- **Retire manual prune route (**BREAKING**)**: remove `POST /backup/manual/prune/cluster/{cluster_id}`, `PruneRequest`, and inventory-scoped pruning.

## Capabilities

### Modified Capabilities

- `api-job-execution`: retires the manual prune endpoint (**BREAKING**) and its request validation requirement, adds `retention` job history retrieval, and adds retention protection and deadline requirements.
- `api-scheduling`: documents that the scheduler tick automatically submits a `retention` job for a recurring full-backup schedule whose pool exceeds its retention count.

## Impact

- **API Changes**: `POST /backup/manual/prune/cluster/{cluster_id}` is retired and returns 404 (**BREAKING**). `GET /job/{id}/history` returns `RetentionHistoryRead` entries for `job_type="retention"`.
- **Database Schema**: new migration adding the `retention_history` table (`job_id`, `ts`, `status`, `message`, `details_json`).
- **Internal Core & DAL**: new DAL modules for retention queries (`dal/metadata/retention.py`), new command module `commands/retention.py` (sweep and `run_retention`), a sweep call in `execute_scheduler_tick`, and `STARROCKS_BR_RETENTION_MAX_SECONDS`. Readers resolving restore lineage (`find_latest_full_backup_job`, `find_latest_full_backup_before`, `list_partitions_for_label`) filter out `deleted_at IS NOT NULL` references.
- **Tests**: legacy prune tests migrated or removed; service tests for the sweep, selection, baseline and restore protection (§22 scenario), independent schedule pools (§11), deadline, and failure isolation.
- **Ordering**: archive `cluster-job-dispatcher` first.
