# Tasks

## 1. Package scaffolding

- [x] 1.1 Create `src/starrocks_br/dal/__init__.py`, `src/starrocks_br/dal/db/__init__.py`, `src/starrocks_br/dal/metadata/__init__.py` and verify `python -c "import starrocks_br.dal.db, starrocks_br.dal.metadata"` succeeds.

## 2. Pure StarRocks-SQL modules (clean lift-and-shift, no logic change)

- [x] 2.1 Move `health.py`'s `check_cluster_health` (`SHOW FRONTENDS`/`SHOW BACKENDS`) into `dal/db/health.py`; update its caller(s) to import from the new location; verify `tests/unit` for health-related tests pass unchanged.
- [x] 2.2 Move `repository.py`'s StarRocks calls (`_find_repository`, `list_repositories`, `build_create_s3_repository_command`, `has_snapshots`, `drop_repository`) into `dal/db/repository.py`; update callers' imports; verify `tests/unit` tests covering repository creation/listing/deletion pass unchanged.

## 3. Pure metadata modules (already-clean ORM, relocation only)

- [x] 3.1 Move `labels.py`'s `determine_backup_label` into `dal/metadata/labels.py`; update callers' imports; verify `tests/unit` tests for label determination pass unchanged.
- [x] 3.2 Move `inventory_groups.py`'s CRUD functions into `dal/metadata/inventory_groups.py`; update callers' imports; verify `tests/unit/crud` tests for inventory groups pass unchanged.
- [x] 3.3 Move `history.py`'s `BackupHistory`/`RestoreHistory` append/query functions into `dal/metadata/history.py`; update callers' imports (`executor.py`, `commands/*`); verify `tests/unit` history tests pass unchanged.
- [x] 3.4 Move `commands/jobs.py`'s direct `Job` ORM access (`submit_job`, `list_jobs`) into `dal/metadata/jobs.py`, leaving `commands/jobs.py` to call through it; verify `tests/unit` job-submission/listing tests pass unchanged.

## 4. prune.py extraction (includes bug fix)

- [x] 4.1 Create `dal/db/prune.py` with `verify_snapshot_exists` and `execute_drop_snapshot`, rewriting the `SHOW SNAPSHOT`/`DROP SNAPSHOT` statements to use `utils.quote_identifier`/`quote_value` for `repository` and `snapshot_name` (fixing the unescaped interpolation currently at `prune.py:168` and `prune.py:191`); update `prune.py` to call through `dal/db/prune.py`, keeping `get_successful_backups`/`cleanup_backup_history` (ORM) in place.
- [x] 4.2 Update `tests/unit` prune tests to assert the now-quoted `SHOW SNAPSHOT`/`DROP SNAPSHOT` statements (matching the pattern used for `test_executor.py`'s `SHOW BACKUP FROM` assertion) and verify they pass.

## 5. executor.py extraction (includes bug fix)

- [x] 5.1 Create `dal/db/backup.py` with `submit_backup_command` and `poll_backup_status`'s StarRocks call, adding `utils.quote_identifier` around `database` in the `SHOW BACKUP FROM` statement (fixing the missing quoting currently at `executor.py:169`); update `executor.py` to call through `dal/db/backup.py`, keeping `execute_backup`'s orchestration (history/concurrency calls) in place.
- [x] 5.2 Update `tests/unit/service/test_executor.py`'s `SHOW BACKUP FROM` assertion to expect the quoted identifier and verify the full test file passes.

## 6. planner.py extraction

- [x] 6.1 Extend `dal/db/backup.py` with `validate_tables_exist`'s `SHOW TABLES FROM` call, `find_recent_partitions`'s `SHOW TABLES FROM`/`SHOW PARTITIONS FROM` calls, and `get_all_partitions_for_tables`'s `information_schema.partitions_meta` query, moved from `planner.py` verbatim (already correctly quoted); verify `tests/unit` planner tests covering these functions pass unchanged.
- [x] 6.2 Move `build_incremental_backup_command` and `build_full_backup_command`'s StarRocks DDL-string construction into `dal/db/backup.py`; update `planner.py` to call through it, keeping `find_latest_full_backup`, `find_tables_by_group`, `record_backup_partitions` (ORM) in place; verify `tests/unit` tests for backup command construction pass unchanged.

## 7. restore.py extraction

- [x] 7.1 Create `dal/db/restore.py` with `get_snapshot_timestamp`'s `SHOW SNAPSHOT` call and `poll_restore_status`'s `SHOW RESTORE FROM` call, moved verbatim (already correctly quoted); verify `tests/unit` restore-status tests pass unchanged.
- [x] 7.2 Move `build_partition_restore_command`, `build_table_restore_command`, `build_database_restore_command`, `_build_restore_command_with_rename`, `_build_restore_command_without_rename`, `_build_partition_restore_command` into `dal/db/restore.py`; update `restore.py` to call through it; verify `tests/unit` tests for restore command construction pass unchanged.
- [x] 7.3 Move `execute_restore`'s `db.execute` call and `_perform_atomic_rename`'s `ALTER TABLE ... RENAME` construction/execution into `dal/db/restore.py`; verify `tests/unit` tests for restore execution and atomic rename pass unchanged.
- [x] 7.4 Split `get_tables_from_backup`: move its `SHOW TABLES FROM` (group-wildcard) StarRocks call into `dal/db/restore.py`, leaving its `session.execute(select(BackupPartition...))` ORM calls in `restore.py`, with `restore.py` composing both; keep `find_restore_pair`, `find_backup_repository`, `get_partitions_from_backup` (ORM) in place; verify `tests/unit` tests for backup-table lookup pass unchanged.

## 8. concurrency.py extraction

- [x] 8.1 Move `_is_backup_job_stale`, `_get_user_databases` (`SHOW DATABASES`), and `_check_backup_job_in_database` (`SHOW BACKUP FROM`, already quoted) into `dal/db/concurrency.py`; update `concurrency.py` to call through it, keeping `reserve_job_slot`, `_get_active_jobs_for_scope`, `_insert_new_job`, `_handle_active_job_conflicts`, `_cleanup_stale_job`, `complete_job_slot` (ORM orchestration) in place, with no change to `reserve_job_slot`'s control flow or the `backup` scope reservation policy; verify `tests/unit` concurrency tests (including staleness/healing and scope-reservation cases) pass unchanged.

## 9. Full-suite verification

- [ ] 9.1 Run the full `tests/unit` suite (crud + service) and confirm all tests pass, with no remaining references to the old module paths for anything moved into `dal/db`/`dal/metadata`; verify via `grep -rn` that `planner.py`, `restore.py`, `executor.py`, `concurrency.py`, `prune.py` no longer contain raw `db.execute`/`db.query`/f-string SQL outside of calls into `dal/db`.
- [ ] 9.2 Manually confirm (by reading the diff) that `prune.py`'s snapshot repository/name and `executor.py`'s backup-status database are now passed through `dal/db` functions that apply `quote_identifier`/`quote_value`, closing the three bugs identified in the proposal.
