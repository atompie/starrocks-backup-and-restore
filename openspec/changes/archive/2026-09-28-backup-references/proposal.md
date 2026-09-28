## Why

A Backup Job currently has no durable, job-scoped record of what it actually backed up or where.
The `backup_partitions` table records the partitions a backup *intends* to cover before it runs,
keyed by `(cluster_id, label)`, and is read by restore planning and prune. It does not distinguish
a job that failed from one that succeeded, so `SPEC.md` §16's rule — "a failed job is not a
recoverable backup" — is not enforced at the data layer, only inferred from `Job.status`
separately. Restore planning also cannot cheaply answer "what did job N actually produce," since
partitions are looked up by cluster+label rather than by job.

Separately, an inventory group spanning more than one database is rejected today
(`MultipleDatabasesInGroupError`), even though `SPEC.md` §5 and `PLAN.md` §0.2 both describe
multi-database inventories as in scope. This blocks a real, already-documented use case.

## What Changes

- Rename/restructure `backup_partitions` into `backup_references`, keyed by `job_id` (FK
  `ON DELETE CASCADE` to `jobs.id`) instead of `(cluster_id, name)`. Add `repository`,
  `snapshot_label`, `snapshot_timestamp`, and `deleted_at` (nullable) columns; keep `database`,
  `table`, `partition` (nullable). Drop `key_hash` and its uniqueness constraint (uniqueness is no
  longer meaningful once rows are scoped to a specific job).
- **BREAKING (internal)**: References are now written only after a backup job's StarRocks
  operation reaches `FINISHED`, not before it runs. A `FAILED` job has zero references, matching
  `SPEC.md` §16. `restore_catalog.py` and `prune.py` move from `(cluster_id, label)` lookups to
  `job_id`-scoped lookups (joined through `Job` where cluster/label context is still needed).
- Add `GET /job/{job_id}/references` returning a job's references, mirroring the existing
  `GET /job/{job_id}/history` endpoint and its 404-on-unknown-job behavior.
- Multi-database inventory backup execution: replace `planner.resolve_group_database`'s
  `MultipleDatabasesInGroupError` rejection with support for one `BACKUP DATABASE` per database in
  the group, all executed and tracked under one Backup Job, with `backup_references` rows recorded
  per database/table.
- Retire the now-superseded `backup_partitions` name and its `(cluster_id, label)`-scoped
  read/write helpers in `dal/metadata/backup_catalog.py`.

## Capabilities

### New Capabilities

(none — the new endpoint extends the existing job-execution capability)

### Modified Capabilities

- `api-job-execution`: adds `GET /job/{job_id}/references`; changes full/incremental backup
  execution to support inventory groups spanning multiple databases (previously rejected) by
  issuing one `BACKUP DATABASE` per database under a single Backup Job.

## Impact

- **Schema**: new Alembic migration on top of head `15e118a64b09`, renaming `backup_partitions` to
  `backup_references`, changing its keying and columns.
- **Code**: `store/models.py` (`BackupPartition` → `BackupReference`), `dal/metadata/backup_catalog.py`
  (writer moves from pre-execution to post-`FINISHED`; reader helpers re-keyed by `job_id`),
  `dal/metadata/restore_catalog.py`, `dal/metadata/prune.py` (queries re-keyed by `job_id`),
  `commands/backup.py` (write references after `executor.execute_backup` succeeds; multi-database
  execution loop), `planner.py` (`resolve_group_database` no longer rejects multi-database groups),
  `api/routes/jobs.py` (new route), `api/schemas` (new `BackupReferenceRead`).
- **Tests**: new `tests/unit/crud/test_backup_references.py` (or renamed from the partitions test),
  updates to `tests/unit/service/` tests covering backup execution and restore/prune that depend on
  the old `(cluster_id, label)` catalog helpers, and a new service-level test for a multi-database
  group backup producing references for both databases and restoring correctly.
