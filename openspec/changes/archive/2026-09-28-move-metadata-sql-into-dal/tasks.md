# Tasks

## 1. Clusters

- [x] 1.1 Create `src/starrocks_br/dal/metadata/clusters.py` with
      `create`, `list_all`, `get`, `update`, `has_active_job`,
      `has_enabled_schedule`, `delete`, lifted from
      `commands/clusters.py` and `api/routes/clusters.py`.
- [x] 1.2 Add `tests/unit/crud/test_dal_clusters.py` covering every
      function above; verify with
      `pytest tests/unit/crud/test_dal_clusters.py -q`.
- [x] 1.3 Add `create_cluster`/`list_clusters`/`get_cluster`/
      `update_cluster` to `commands/clusters.py` calling the new DAL, and
      rewrite `delete_cluster` to use `has_active_job`/
      `has_enabled_schedule`/`delete` instead of inline
      `session.query(...)`, preserving guard order and raised
      exceptions; verify with
      `pytest tests/unit/service/test_commands_clusters.py -q`.
- [x] 1.4 Update `api/routes/_cluster_connect.py::get_cluster_or_404` to
      call `dal.metadata.clusters.get`, and update
      `create_cluster`/`list_clusters`/`update_cluster` in
      `api/routes/clusters.py` to call `commands/clusters.py` (keep the
      `IntegrityError` -> 409 translation in the route); verify with
      `pytest tests/unit/service/test_api_clusters.py tests/unit/service/test_cluster_connect.py -q`.

## 2. Schedules

- [x] 2.1 Create `src/starrocks_br/dal/metadata/schedules.py` with
      `create`, `list_for_cluster`, `get`, `update`, `delete`,
      `due_schedules`, and `advance_next_run_at` (the conditional
      `UPDATE ... WHERE next_run_at == previously_due_at`, returning
      `rowcount`), lifted from `api/routes/schedules.py` and
      `commands/schedules.py::run_due_schedules`.
- [x] 2.2 Add `tests/unit/crud/test_dal_schedules.py` covering every
      function above, including `advance_next_run_at`'s idempotency
      (second call with a stale `previously_due_at` returns
      `rowcount == 0`); verify with
      `pytest tests/unit/crud/test_dal_schedules.py -q`.
- [x] 2.3 Add `create_schedule`/`list_schedules`/`get_schedule`/
      `update_schedule`/`delete_schedule` to `commands/schedules.py`
      calling the new DAL, and rewrite `run_due_schedules` to call
      `due_schedules`/`advance_next_run_at` instead of its inline
      `session.query`/`session.execute(update(...))`, preserving the
      per-occurrence idempotency behavior described in its docstring;
      verify with `pytest tests/unit/service/test_commands_schedules.py -q`
      (an existing commands-level test file already covers
      `run_due_schedules` - extended coverage was unnecessary since
      `create_schedule`/`list_schedules`/etc. are exercised end-to-end via
      `test_api_schedules.py`).
- [x] 2.4 Update every route in `api/routes/schedules.py` to call
      `commands/schedules.py` instead of `db.get`/`db.add`/`db.flush`/
      `db.query`/`db.delete` directly, keeping the existing 404/422
      HTTP translation and the `inventory_groups.group_exists`/
      `ensure_repository_exists` pre-checks in the route; verify with
      `pytest tests/unit/service/test_api_schedules.py -q`.

## 3. Jobs (status transitions and lookups)

- [x] 3.1 Extend `src/starrocks_br/dal/metadata/jobs.py` with `get`,
      `mark_running`, `mark_progress`, `mark_failed`, `mark_success`, and
      `set_label`, matching the exact fields each call site in
      `jobs/thread_backend.py` and `commands/backup.py::_set_job_label`
      sets today. Also added `list_history_for_job` (job-type -> history
      model routing) for task 3.5.
- [x] 3.2 Add the new functions to `tests/unit/crud/test_dal_jobs.py`
      (created - none existed); verify with
      `pytest tests/unit/crud/test_dal_jobs.py -q`.
- [x] 3.3 Update `jobs/thread_backend.py`'s four `session.get(Job,
      job_id)` call sites (`_make_progress_callback`, `_run_job`'s
      start/failure/success paths) to call the new DAL functions instead,
      keeping the surrounding `session_scope()` blocks and control flow
      unchanged; verify with
      `pytest tests/unit/service/test_jobs_thread_backend.py -q`.
- [x] 3.4 Update `commands/backup.py::_set_job_label` to call
      `dal.metadata.jobs.set_label`; verify with
      `pytest tests/unit/service/test_commands_backup.py -q`.
- [x] 3.5 Add `get_job`/`get_job_history` to `commands/jobs.py` calling
      `dal.metadata.jobs.get`/`list_history_for_job`, and update
      `api/routes/jobs.py`'s `get_job`/`get_job_history` routes to call
      them instead of `db.get(Job, ...)`/`db.scalars(select(model)...)`
      directly, keeping the existing 404 and job-type-to-history-model
      branching (now inside the DAL) behavior identical; verify with
      `pytest tests/unit/service/test_api_jobs.py -q`.

## 4. Prune

- [x] 4.1 Create `src/starrocks_br/dal/metadata/prune.py` with
      `get_successful_backups` (the `Job`/`BackupPartition`/
      `TableInventory` join `session.execute(select(...))`) and
      `cleanup_backup_history`, lifted verbatim from `prune.py` (the
      logging/try-except wrapper around `cleanup_backup_history` stayed
      in `prune.py` - it's orchestration, not data access).
- [x] 4.2 Add `tests/unit/crud/test_dal_prune.py` covering both
      functions; verify with `pytest tests/unit/crud/test_dal_prune.py -q`.
- [x] 4.3 Update `prune.py` to call through the new DAL module for both
      functions, leaving `filter_snapshots_to_delete` and the
      `dal/db/prune.py` StarRocks pass-throughs unchanged; verify with
      `pytest tests/unit/service/test_prune.py -q`.

## 5. Restore catalog

- [x] 5.1 Create `src/starrocks_br/dal/metadata/restore_catalog.py` with
      `find_successful_job` (shared by `find_restore_pair` and
      `find_backup_repository`), `find_latest_full_backup_before`,
      `list_partitions_for_label` and `list_group_table_memberships`
      (the two ORM halves of `get_tables_from_backup`), and
      `list_partition_names` (for `get_partitions_from_backup`), lifted
      from `restore.py`. Function names differ slightly from the
      original plan (split into two more targeted queries instead of one
      `list_backup_partitions_for_group`) but cover the same call sites.
- [x] 5.2 Add `tests/unit/crud/test_dal_restore_catalog.py` covering all
      five functions; verify with
      `pytest tests/unit/crud/test_dal_restore_catalog.py -q`.
- [x] 5.3 Update `restore.py`'s `find_restore_pair`,
      `find_backup_repository`, `get_tables_from_backup`, and
      `get_partitions_from_backup` to call through the new DAL module,
      keeping `get_tables_from_backup`'s composition of its StarRocks
      call (`dal/db/restore.py::show_tables`) and its ORM calls in place,
      and keeping the exception-mapping/filtering business logic in
      `restore.py`; verify with `pytest tests/unit/service/test_restore.py -q`.

## 6. Backup catalog (planner)

- [x] 6.1 Create `src/starrocks_br/dal/metadata/backup_catalog.py` with
      `find_latest_full_backup_job`, `find_successful_job_by_label` (the
      baseline-job lookup inside `find_recent_partitions`),
      `list_group_table_memberships`, and `record_partitions`, lifted
      from `planner.py`.
- [x] 6.2 Add `tests/unit/crud/test_dal_backup_catalog.py` covering all
      four functions; verify with
      `pytest tests/unit/crud/test_dal_backup_catalog.py -q`.
- [x] 6.3 Update `planner.py`'s `find_latest_full_backup`,
      `find_tables_by_group`, `find_recent_partitions`, and
      `record_backup_partitions` to call through the new DAL module,
      keeping `resolve_group_database`, `validate_tables_exist`,
      `find_recent_partitions`'s StarRocks calls, and the backup-command
      builders unchanged; verify with
      `pytest tests/unit/service/test_planner.py -q`.

## 7. Concurrency

- [x] 7.1 Create `src/starrocks_br/dal/metadata/concurrency.py` with
      `active_jobs_for_scope`, `insert_active_job`, `cancel_stale_job`,
      and `complete_job` (the `RunStatus` queries/writes from
      `_get_active_jobs_for_scope`, `_insert_new_job`,
      `_cleanup_stale_job`, `complete_job_slot`), lifted from
      `concurrency.py`.
- [x] 7.2 Add `tests/unit/crud/test_dal_concurrency.py` covering all four
      functions; verify with
      `pytest tests/unit/crud/test_dal_concurrency.py -q`.
- [x] 7.3 Update `concurrency.py` to call through the new DAL module for
      the four functions above, keeping `reserve_job_slot`,
      `_handle_active_job_conflicts`, `_can_heal_stale_job`, and
      `_raise_concurrency_conflict` (decision logic, calls into both this
      new module and the existing `dal/db/concurrency.py`) unchanged;
      verify with `pytest tests/unit/service/test_concurrency.py -q`.

## 8. Remaining single-call-site fixes

- [x] 8.1 Update `api/routes/inventory_groups.py::get_inventory_group`'s
      `db.get(InventoryGroup, group_id)` to call
      `dal.metadata.inventory_groups.get_group_row` (added to the
      existing `dal/metadata/inventory_groups.py`); verify with
      `pytest tests/unit/crud/test_inventory_groups_sql.py tests/unit/service/test_api_inventory_groups.py -q`.

## 9. Full-suite verification

- [x] 9.1 Run the full unit suite (`pytest tests/unit -q`) and verify it
      passes with no behavior changes. All ~655 tests pass.
- [x] 9.2 Grep for remaining direct metadata access outside
      `dal/metadata/` (`grep -rnE "session\.(execute|query|add|delete|scalars|scalar|get)\(|db\.(query|add|flush|delete|execute|scalars|scalar|get)\(" src/starrocks_br --include=*.py | grep -v '/dal/'`)
      - returns zero matches: every remaining SQLAlchemy call site in
      `src/starrocks_br/` outside `dal/` is gone.
