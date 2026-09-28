## Context

`BackupPartition` (`store/models.py:206-221`, table `backup_partitions`) is written by
`dal/metadata/backup_catalog.record_partitions`, called from `commands/backup.py` **before**
`executor.execute_backup` runs (full: line 95, incremental: line 168), inside the same
`session_scope()` that also reserves the concurrency slot. Rows are keyed by `(cluster_id,
key_hash)` where `key_hash` hashes `label|database|table|partition_name`; there is no `job_id`
column and no link to whether the job that wrote them ever succeeded.

Three call sites read `BackupPartition` by `(cluster_id, label)`:
`restore_catalog.list_partitions_for_label`/`list_partition_names` (restore lineage) and
`prune.get_successful_backups`/`cleanup_backup_history` (prune eligibility and cleanup). All three
already have access to the owning `Job` row (by label) when they need cluster/label context, so
re-keying by `job_id` does not lose any information they currently use.

`planner.resolve_group_database` (`planner.py:53-73`) raises `MultipleDatabasesInGroupError` when
an inventory group's memberships span more than one database; `commands/backup.py` calls it once
per full/incremental run before building the single-database `BACKUP` command. See proposal.md for
why both changes are being made together.

## Goals / Non-Goals

**Goals:**
- Make "does this job have recoverable data" answerable directly from `backup_references.job_id`
  and `Job.status`, without inferring it from label matching.
- Support multi-database inventory groups in backup execution, one `BACKUP DATABASE` per database
  under a single Backup Job.

**Non-Goals:**
- Not fixing the open session-held-during-polling issue in `commands/backup.py` /
  `executor.execute_backup` (`AGENTS.md`, PLAN.md §7) — this change writes references in a new
  short-lived session after `execute_backup` returns, but does not restructure the existing
  session that wraps the poll itself.
- Not implementing retention (PLAN.md §10) or restore-by-`source_job_id` (PLAN.md §11); those
  sections consume `backup_references` later but are out of scope here. `restore.py`'s existing
  label-based restore path is only re-pointed at the renamed table, not redesigned.
- Not changing multi-database *restore* — restoring a job that covered multiple databases already
  works through `restore_catalog.list_partitions_for_label`'s per-database grouping once that
  query is re-keyed by `job_id`; no new restore command logic is added.

## Decisions

### 1. Rename in place, migrate data, keep one table
`backup_partitions` is renamed to `backup_references` via `op.rename_table` rather than creating a
parallel table. Columns added: `job_id` (FK `ON DELETE CASCADE` to `jobs.id`, `NOT NULL`,
indexed), `repository` (`String(255)`, `NOT NULL` — resolved from `Job.repository`, matching how
`prune.get_successful_backups` already reads it), `snapshot_label` (`String(255)`, `NOT NULL` —
same value as today's `label` column, kept as its own column since `label` also still exists on
`Job`), `snapshot_timestamp` (`DateTime(timezone=True)`, `NOT NULL`), `deleted_at`
(`DateTime(timezone=True)`, nullable — unset until PLAN.md §10's retention job starts using it).
Removed: `key_hash` and `uq_backup_partitions_cluster_key_hash` (uniqueness by content hash is
meaningless once a row belongs to exactly one job — a job either wrote a reference or it didn't).
`cluster_id` is dropped as a stored column; it's reachable via `job_id → Job.cluster_id` and every
existing reader already joins through `Job` for other fields.

Migration data step: for each existing `backup_partitions` row, resolve `job_id` by looking up the
`Job` with matching `(cluster_id, label)` and `status = 'SUCCESS'` (the only rows that matter per
`SPEC.md` §16); rows whose label doesn't resolve to a successful job (orphaned/failed-job leftovers
that predate this change enforcing "no references on failure") are dropped rather than migrated.
`repository` and `snapshot_timestamp` backfill from that same `Job` row (`Job.repository`,
`Job.finished_at`).

Alternative considered: keep `backup_partitions` untouched and add a new `backup_references` table,
migrating callers gradually. Rejected per the earlier user decision — it leaves two tables
describing overlapping data with no follow-up section in `PLAN.md` to reconcile them.

### 2. Write references after `FINISHED`, not before execution
`commands/backup.py` currently calls `record_backup_partitions` before
`executor.execute_backup`. This moves to a new `session_scope()` block placed after the
`if not result["success"]: _raise_for_backup_failure(result)` check, in both `run_backup_full` and
`run_backup_incremental`. The pre-execution `planner.get_all_partitions_for_tables` /
`find_recent_partitions` calls still run before execution (they build the `BACKUP` command itself),
but their output is now held in a local variable and only persisted as references after success is
confirmed. A failed job therefore writes zero references, satisfying `SPEC.md` §16 directly instead
of relying on callers to also check `Job.status`.

### 3. Re-key the three existing readers by `job_id`
- `restore_catalog.list_partitions_for_label` / `list_partition_names`: first resolve the `Job` by
  `(cluster_id, label)` (as `find_successful_job` already does), then query `BackupReference` by
  `job_id=job.id` instead of `(cluster_id, label)`.
- `prune.get_successful_backups`: change the join from `BackupPartition.label == Job.label` to
  `BackupReference.job_id == Job.id`; drop the now-redundant `BackupPartition.cluster_id ==
  cluster_id` filter (implied by the join).
- `prune.cleanup_backup_history`: the explicit `BackupPartition.__table__.delete()` becomes
  unnecessary — deleting the `Job` row now cascades to `backup_references` via the new FK, the same
  way it already cascades to `backup_history`/`restore_history`. The function keeps deleting the
  `Job` row; the explicit reference delete is removed as dead code once the cascade covers it.

### 4. Multi-database execution as a per-database loop under one Job
`resolve_group_database` is replaced by a new `planner.resolve_group_databases` returning the
sorted list of distinct databases in the group (length 1 today, length N once multi-db groups are
allowed). `commands/backup.py` loops over that list, building and executing one `BACKUP DATABASE`
command per database, all under the same `job_id`, before returning. `executor.execute_backup` is
called once per database; if any database's operation fails, the job is marked `FAILED` overall
(first failure stops the loop — partial per-database success is not exposed as a partial job
result, matching `SPEC.md` §13's "a job is a historical fact" framing: one job, one outcome).
References are written per database as each one's `execute_backup` call succeeds, inside the same
per-database iteration, before moving to the next database.

`labels.determine_backup_label` and `find_recent_partitions`/`find_latest_full_backup` are already
scoped to a single `database` parameter; they're called once per database in the loop rather than
being restructured to accept a list.

Alternative considered: run all databases inside a single `BACKUP` command using StarRocks
multi-database snapshot syntax. Rejected — `SHOW BACKUP`/history polling and label management in
this codebase are already built around one label per one `BACKUP` statement; per-database looping
reuses that machinery unchanged instead of redesigning the label/history model.

### 5. Baseline resolution moves from `Job.label` prefix-matching to `BackupReference.database_name`
`labels.determine_backup_label` derives a database-scoped label
(`f"{database_name}_{today}_{backup_type}"`), and two existing lookups relied on that by matching
`Job.label.like(f"{database}_%")` to find "the latest full backup for this database":
`backup_catalog.find_latest_full_backup_job` (used by `find_latest_full_backup`, which resolves an
incremental's baseline) and `restore_catalog.find_latest_full_backup_before` (used by
`restore.py` to pick the base full backup for a point-in-time restore). Once one Job can cover
several databases under the per-database loop (Decision 4), `Job.label` holds only one of those
databases' labels — the label-prefix match would silently miss the right baseline for every other
database the Job covered.

Both lookups are changed to join `BackupReference` and filter on `BackupReference.database_name`
instead of pattern-matching `Job.label`. This works identically for a single-database Job (today's
case) and correctly for a multi-database one, since every database a Job covers has its own
reference rows regardless of what `Job.label` holds. `Job.label` keeps recording one representative
label (the first database processed in the loop) for logging/display; it is no longer used to
answer "which databases did this job cover."

Alternative considered: restrict multi-database support to full backups only, leaving incremental
backups against a multi-database group unsupported, to avoid touching baseline resolution at all.
Rejected by the user - PLAN.md §6.5 does not distinguish full from incremental, and leaving
incremental broken for multi-database groups would be a materially narrower version of the
proposed capability.

Fixing this also surfaced a latent bug in `restore.find_restore_pair`: it derived an incremental
backup's database name via `target_label.split("_")[0]`, which silently truncates any database
name containing an underscore (e.g. `sales_db` becomes `sales`). It happened to keep working only
because the old lookup used a loose `Job.label.like(f"{database_name}_%")` prefix match, where the
truncated prefix still matched the full label. Once that lookup became an exact
`BackupReference.database_name` match, the truncation broke it. Fixed by reading the *target*
incremental job's own database name from its `backup_references` rows
(`restore_catalog.list_partitions_for_label`) instead of parsing it out of the label string.

## Risks / Trade-offs

- **[Risk]** Multi-database backup takes N sequential StarRocks operations instead of one →
  **Mitigation**: acceptable per `PLAN.md` §6.5's own framing ("one `BACKUP DATABASE` per
  database... under one Backup Job"); a future change can parallelize if this becomes a bottleneck,
  but it's out of scope here.
- **[Risk]** A multi-database job that fails partway (database 2 of 3) leaves references for
  database 1 in `backup_references` even though the job's overall status is `FAILED` →
  **Mitigation**: since the requirement is "a failed job has no *restorable* references," and
  restore always resolves the full set of a job's references before restoring, a restore attempt
  against a `FAILED` job is already rejected at the `Job.status` check (existing behavior,
  `SPEC.md` §16) regardless of what partial reference rows exist; the rows are inert until a future
  change adds job-status-gated cleanup.
- **[Risk]** Migrating existing `backup_partitions` rows to resolve `job_id` may drop legitimate
  history if a label collision or renamed cluster prevents matching a `Job` → **Mitigation**: this
  only affects pre-migration data in existing deployments; document the migration's drop condition
  in its docstring/message so an operator can inspect row counts before/after if needed.

## Migration Plan

1. Add the Alembic migration (rename table, alter columns, backfill `job_id`/`repository`/
   `snapshot_timestamp`, drop `key_hash`) on top of head `15e118a64b09`.
2. Update `store/models.py` (`BackupPartition` → `BackupReference`).
3. Update `dal/metadata/backup_catalog.py`, `restore_catalog.py`, `prune.py` to the new schema and
   `job_id`-keyed queries.
4. Update `commands/backup.py` to defer the write until after success, and to loop over
   `resolve_group_databases`.
5. Add `GET /job/{job_id}/references` (route + schema), mirroring the `/history` route.
6. Update/add tests (CRUD for `backup_references`, service-level for failed-job-has-no-references
   and multi-database-job-has-references-for-both).

Rollback: revert the migration (`alembic downgrade`) and the accompanying code changes together;
there is no independent feature flag since this changes on-disk schema and write timing together.
