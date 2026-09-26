## 1. Schema and migration

- [x] 1.1 Remove `database` and `repository` columns from `Cluster` in `store/models.py`, and verify `pytest` model/store tests still collect without import errors
- [x] 1.2 Add an Alembic migration dropping `clusters.database` and `clusters.repository`, and verify `alembic upgrade head` then `alembic downgrade -1` both run cleanly against a fresh SQLite metastore

## 2. Cluster API schemas and routes

- [x] 2.1 Remove `database`/`repository` from `ClusterCreate`, `ClusterUpdate`, `ClusterRead` in `api/schemas.py`, and constrain `default_backend` on `ClusterCreate`/`ClusterUpdate` to `Literal["thread", "job"]`, verifying invalid values raise a 422 in a schema-level unit test
- [x] 2.2 Update `api/routes/clusters.py` to stop passing `database`/`repository` through on create/update, and verify the cluster-registry route tests pass with the new required/optional field set
- [x] 2.3 Update `api/routes/_cluster_connect.py` and the connection-verify path to stop reading `cluster.database`, verifying a registered-cluster verification request no longer references a stored database (only host/port/user/password)
- [ ] 2.4 Update `specs/api-cluster-registry/spec.md` scenarios' backing tests (registration, list, update, verify) to match the new field set, and verify the full cluster-registry test suite passes

## 3. Backup job repository field

- [x] 3.1 Add a required `repository: str` field to `BackupFullRequest` and `BackupIncrementalRequest` in `api/schemas.py`, verifying a request missing it is rejected with 422
- [x] 3.2 In `api/routes/jobs.py`, add a synchronous live-repository-existence check (reusing the lookup `api-repository-management` already uses to list repositories) before creating a full/incremental backup job, returning 404 if the named repository does not exist on the target cluster, and verify with a test against an unknown repository name
- [x] 3.3a Add a `planner` (or `inventory_groups`) helper that resolves a group's single database from its distinct `table_inventory.database_name` values, raising a clear error if the group spans more than one database, and verify with a unit test covering the single-database, zero-membership, and multi-database cases
- [x] 3.3b Update `commands/backup.py` (`run_backup_full`, `run_backup_incremental`) to resolve the group's database via 3.3a instead of `cluster.database`, and to take `repository` from the job's params instead of `cluster.repository`, verifying a full-backup integration test using an explicit repository still produces the same backup label/history/snapshot as before
- [x] 3.3c Fix `planner.validate_tables_exist` to raise (via `InvalidTablesInInventoryError` or a new exception) when a group's memberships reference more than one database, instead of silently filtering out the mismatched rows, and verify with a regression test
- [ ] 3.4 Add a required `repository` column to `Schedule` in `store/models.py` and a matching Alembic migration, and add a required `repository: str` field to `ScheduleCreate`/`ScheduleUpdate`/`ScheduleRead` in `api/schemas.py`, verifying a schedule-creation request missing it is rejected with 422
- [ ] 3.5 In `api/routes/schedules.py`, add the same synchronous live-repository-existence check used for backup job submission before creating or updating a schedule, returning 404 for an unknown repository, and verify with a test
- [ ] 3.6 Update `commands/schedules.py`'s `run_due_schedules` to pass `schedule.repository` through in the job params it builds for `submit_job`, verifying a due-schedule integration test produces a job whose backup uses that repository

## 4. Restore database field and repository inference

- [ ] 4.1 Add an optional `database: str | None` field to `RestoreRequest` in `api/schemas.py`, with a validator requiring it if and only if `table` is set, verifying both the "table without database" 422 case and the "table with database" success case
- [ ] 4.2 Update `commands/restore.py` to take `database` from `params` (only used when `table` is set) instead of `cluster.database`, and to resolve the restore's repository from `backup_history.repository` for the target label instead of `cluster.repository`, verifying an existing restore integration test still passes unchanged
- [ ] 4.3 Update `specs/api-job-execution/spec.md`-backed restore tests for the new `database` requirement, and verify the full restore test suite passes

## 5. Prune group scoping and per-snapshot repository

- [x] 5.1 Make `group_id` required (non-optional) on `PruneRequest` in `api/schemas.py`, verifying a request without it is rejected with 422
- [x] 5.2 In `api/routes/jobs.py`, add the same synchronous group-exists check used for backup requests to prune submission, returning 404 for an unknown `group_id`, and verify with a test
- [ ] 5.3 Update `prune.get_successful_backups` (or its caller in `commands/prune.py`) to also select each row's `repository` from `backup_history`, and update `commands/prune.py`'s `keep_last`/`older_than` branches to drop each snapshot using its own resolved repository instead of `cluster.repository`, verifying a prune integration test across a single group still deletes the expected snapshots
- [ ] 5.4 Update the `snapshot`/`snapshots` named-strategy branch in `commands/prune.py` to resolve each named snapshot's repository from the group-scoped `backup_history` lookup before calling `prune.verify_snapshot_exists` against that resolved repository (instead of `cluster.repository`), verifying a test that a named snapshot outside the request's group is rejected as not found
- [ ] 5.5 Update `specs/api-job-execution/spec.md`-backed prune tests for the required `group_id`, and verify the full prune test suite passes

## 6. Job/schedule backend enum

- [ ] 6.1 Constrain the `backend` field on `BackupFullRequest`, `BackupIncrementalRequest`, `RestoreRequest`, `PruneRequest`, `ScheduleCreate`, and `ScheduleUpdate` in `api/schemas.py` to `Literal["thread", "job"] | None`, verifying an unrecognized value is rejected with 422 independent of the existing enabled-backend check
- [ ] 6.2 Verify (with an existing or new test) that a recognized-but-disabled backend value (e.g. `"job"` when only `"thread"` is enabled) still produces its own distinct 422 from `api/routes/jobs.py`'s enabled-backend check, not the schema-level enum check

## 7. CLI flags

- [ ] 7.1 Update `cli.py`'s cluster-registration/update commands to drop `--database`/`--repository` flags and add/validate a `--default-backend` choice restricted to `thread`/`job`
- [ ] 7.2 Update the API-client CLI's backup/restore/prune subcommands to add `--repository` (backup) and `--database` (restore, paired with `--table`) flags, and to make `--group` required for prune, verifying `starrocks-br api job ...` help text and a smoke-tested invocation reflect the new required flags
- [ ] 7.3 Update the API-client CLI's schedule-add/update subcommands to add a required `--repository` flag, verifying help text and a smoke-tested invocation reflect it

## 8. End-to-end verification

- [ ] 8.1 Run the full test suite (`pytest`) and verify it passes with no remaining references to `cluster.database`/`cluster.repository` (`grep -rn "cluster.database\|cluster.repository" src/` returns nothing)
- [ ] 8.2 Run `openspec validate decouple-database-and-repository-from-cluster --strict` and verify it reports the change as valid
