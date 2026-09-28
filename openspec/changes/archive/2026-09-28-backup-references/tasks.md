## 1. Schema

- [x] 1.1 Add an Alembic migration on top of head `15e118a64b09` that renames `backup_partitions`
  to `backup_references`, adds `job_id` (FK `ON DELETE CASCADE` to `jobs.id`, `NOT NULL`, indexed),
  `repository` (`String(255)`, `NOT NULL`), `snapshot_label` (`String(255)`, `NOT NULL`),
  `snapshot_timestamp` (`DateTime(timezone=True)`, `NOT NULL`), `deleted_at`
  (`DateTime(timezone=True)`, nullable), drops `key_hash`, `cluster_id`, and
  `uq_backup_partitions_cluster_key_hash`; backfills `job_id`/`repository`/`snapshot_timestamp` for
  existing rows by matching `(cluster_id, label)` to a `SUCCESS` `Job`, dropping rows that don't
  resolve. Verified with `alembic upgrade head` against a populated SQLite DB
  (success/failed/orphaned rows) and `alembic downgrade -1` / re-`upgrade head` round-trips
  cleanly.
- [x] 1.2 Update `store/models.py`: rename `BackupPartition` to `BackupReference`, table
  `backup_references`, with the new column set from 1.1. `python -m pytest tests/unit --collect-only`
  now fails collection in the modules that still import the old `BackupPartition`/`record_partitions`
  names, as expected until tasks 2.x/3.x update them.

## 2. DAL

- [x] 2.1 Update `dal/metadata/backup_catalog.py`: rename `record_partitions` to
  `record_references`, taking `job_id`, `repository`, `snapshot_label`, `snapshot_timestamp`, and
  the partition list, writing one `BackupReference` row per partition (or one row with
  `partition=None` for a whole-database reference — match today's per-partition granularity).
  Also updated `planner.resolve_group_database` → `resolve_group_databases` (multi-db, task 3.1
  pulled forward since `validate_tables_exist`'s cross-database rejection had to go with it) and
  removed the now-dead `MultipleDatabasesInGroupError`. Verified with rewritten
  `tests/unit/crud/test_backup_references.py` (renamed from `test_dal_backup_catalog.py`):
  `python -m pytest tests/unit/crud/test_backup_references.py -q --no-cov` — 6 passed.
- [x] 2.2 Update `dal/metadata/restore_catalog.py`: `list_partitions_for_label` and
  `list_partition_names` resolve the owning `Job` first (as `find_successful_job` already does),
  then query `BackupReference` by `job_id`. Verified `tests/unit/crud/test_dal_restore_catalog.py`
  updated and passing (`python -m pytest tests/unit/crud/test_dal_restore_catalog.py -q --no-cov`).
- [x] 2.3 Update `dal/metadata/prune.py`: `get_successful_backups` joins `BackupReference` on
  `job_id == Job.id` instead of `label == Job.label`; `cleanup_backup_history` drops its explicit
  `BackupPartition` delete (now covered by the FK cascade when the `Job` row is deleted, confirmed
  enforced via the existing `PRAGMA foreign_keys=ON` connect listener). Verified
  `tests/unit/crud/test_dal_prune.py` updated and passing
  (`python -m pytest tests/unit/crud/test_dal_prune.py -q --no-cov`).

## 3. Backup execution

- [x] 3.0 (Added during implementation, see design.md Decision 5) Change
  `backup_catalog.find_latest_full_backup_job` and `restore_catalog.find_latest_full_backup_before`
  to resolve the baseline full backup via `BackupReference.database_name` (joined on `job_id`)
  instead of `Job.label.like(f"{database}_%")`, since a multi-database Job's `Job.label` can only
  hold one database's label. Verified with new unit tests
  (`test_find_latest_full_backup_job_resolved_by_reference_not_label`,
  `test_find_latest_full_backup_before_resolved_by_reference_not_label`) covering a job whose
  `Job.label` does not start with the target database's name but has a `BackupReference` row for
  it; existing `test_planner.py` tests updated to seed a matching reference. 62/62 pass across
  `test_planner.py`, `test_backup_references.py`, `test_dal_restore_catalog.py`, `test_dal_prune.py`.

- [x] 3.1 Add `planner.resolve_group_databases(session, cluster_id, group_id) -> list[str]`
  returning the sorted distinct databases in the group (replaces `resolve_group_database`);
  `MultipleDatabasesInGroupError` was unused after removing its only call site
  (`validate_tables_exist`'s cross-database rejection) and its own definition, so it was deleted
  from `exceptions.py` rather than kept as dead code. Verified with
  `tests/unit/service/test_planner.py::test_resolve_group_databases_single_database`,
  `test_resolve_group_databases_multiple_databases`,
  `test_resolve_group_databases_raises_when_group_has_no_tables` — all pass (42/42 in the file).
- [x] 3.2 Update `commands/backup.py::run_backup_full` and `run_backup_incremental` to loop over
  `resolve_group_databases`, running the existing per-database label/command/execute steps for each
  database under the same `job_id`; stop and fail the job on the first database's failure.
  Discovered during implementation that the concurrency slot (reserved once, per PLAN.md's
  one-backup-per-cluster policy) could not be released correctly by `executor.execute_backup`'s
  existing internal `complete_job_slot` call once a job spans several databases with several
  different per-database labels - added `release_slot: bool = True` to `execute_backup`
  (`False` for every per-database call in the loop) so `commands/backup.py` reserves and releases
  the slot exactly once for the whole job, regardless of how many databases it covers. `Job.label`
  and `Job.baseline_job_id` (single columns) record the first database processed; a custom `name`
  gets a `_{database}` suffix for the 2nd+ database to keep StarRocks snapshot labels distinct.
  Verified with `tests/unit/service/test_commands_backup.py` (updated for the renamed
  `record_backup_references` mock) - all 8 tests pass; full unit suite 681 passed.
- [x] 3.3 Move the reference-writing call in both functions from before `executor.execute_backup`
  to a new `session_scope()` block immediately after each database's `execute_backup` call reports
  success, using `record_references` from 2.1 (`snapshot_timestamp` taken as
  `datetime.now(UTC)` at that point, since `Job.finished_at` isn't set until after
  `commands/backup.py` returns). Verified via the same `test_commands_backup.py` run above; a
  failing database's `execute_backup` call raises before `record_backup_references` is reached for
  that or any later database, so a failed job accumulates no references beyond whatever earlier
  databases in the loop already succeeded (see design.md Risks).

## 4. API

- [x] 4.1 Add `GET /job/{job_id}/references` to `api/routes/jobs.py`, mirroring the `/history`
  route's 404-on-unknown-job shape, backed by a new `commands/jobs.get_job_references` →
  `dal/metadata/jobs.list_references_for_job` (mirrors `list_history_for_job`'s shape). Added
  schema `BackupReferenceRead` (id/job_id/repository/snapshot_label/snapshot_timestamp/
  database/table/partition). Verified with 4 new tests in `tests/unit/service/test_api_jobs.py`
  (404 for unknown job, `[]` for still-PENDING, recorded rows for a job whose handler calls
  `record_references`, `[]` for a FAILED job) - all pass; full unit suite 685 passed.

## 5. Tests and docs

- [x] 5.1 Add/rename `tests/unit/crud/test_backup_references.py` covering `record_references` and
  the re-keyed `restore_catalog`/`prune` reads, per AGENTS.md's CRUD-vs-service split. Done in 2.1
  (renamed from `test_dal_backup_catalog.py`, no license header carried over); `restore_catalog`
  and `prune` reads covered in their own existing CRUD files (`test_dal_restore_catalog.py`,
  `test_dal_prune.py`), updated in 2.2/2.3/3.0.
- [x] 5.2 Add a service-level test asserting a successful job has references and a failed job has
  none (SPEC.md §16), and a service-level test asserting a two-database group backup produces
  references for both databases. Added to `tests/unit/service/test_commands_backup.py`:
  `test_run_backup_full_records_no_references_on_failure`,
  `test_run_backup_full_multi_database_writes_references_for_both_databases`,
  `test_run_backup_full_multi_database_stops_and_keeps_only_prior_successes_on_failure` (using a
  real in-memory `sqlite_session` in place of the file's usual `fake_session` Mock, so
  `record_backup_references` actually persists rows the test can assert against). End-to-end
  restore of a multi-database job is exercised at the integration level (5.4), not here - these are
  unit-level and mock `executor.execute_backup`/StarRocks itself.
- [x] 5.3 Run the full unit suite and fix any fallout from the rename/re-keying. Verified:
  `python -m pytest tests/unit` → 688 passed.
- [x] 5.4 Run integration tests against the local StarRocks/MinIO stack. Verified:
  `python -m pytest tests/integration` → 22 passed (stack was reachable, not a clean skip),
  including `test_full_backup_restore_cycle.py::test_full_backup_then_restore_recovers_dropped_database`
  which exercises the full backup → drop database → restore path end-to-end through the new
  post-`FINISHED` reference-writing and re-keyed restore lookup. No dedicated multi-database
  integration test was added (PLAN.md §6.6's multi-database restore scenario) - existing
  integration fixtures/inventories in this suite are single-database; adding a multi-database
  fixture is separable follow-up, not blocking this change's unit-level coverage of the same
  behavior (5.2).
