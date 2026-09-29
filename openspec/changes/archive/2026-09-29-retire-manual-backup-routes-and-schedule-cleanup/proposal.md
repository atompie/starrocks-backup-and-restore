## Why

Manual full backups already have a one-shot schedule mechanism, while direct manual routes create backup jobs outside that lifecycle. Schedule deletion currently removes only metadata and cannot safely remove repository snapshots. This change makes schedules the sole backup submission path and makes schedule deletion and expiry tracked cleanup operations.

## What Changes

- **BREAKING** Retire the manual full and incremental backup submission routes; use one-shot schedules for immediate full backups and recurring schedules for incremental backups.
- Change schedule deletion from immediate `204` removal to synchronous validation followed by an asynchronous `schedule_cleanup` job and `202` response.
- Have cleanup drop each distinct referenced snapshot, then delete the schedule's backup jobs and their histories, references, and dependent restore jobs before deleting the schedule.
- Expire eligible one-shot schedules through the same cleanup path; blocked expiry attempts are logged and retried on a later scheduler tick.
- Prevent accepted deletion from racing with due-schedule dispatch or producing duplicate cleanup jobs.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-job-execution`: retire direct backup submission routes and track schedule cleanup jobs and backup-to-restore dependency deletion.
- `api-scheduling`: specify asynchronous schedule deletion, validation conflicts, and automatic one-shot expiry cleanup.

## Impact

Affected areas include the job type registry and handlers, schedule commands and routes, scheduler tick, metadata models and DAL, StarRocks snapshot deletion, and API job/schedule specifications. Cleanup must deduplicate manifest rows by `(repository, snapshot_label)`. A durable restore source-backup relationship is needed because restore requests currently keep `target_label` in job parameters rather than a foreign key. Schedule deletion and due dispatch must coordinate through metadata so a schedule cannot submit work after cleanup is accepted. No new external dependency is required.
