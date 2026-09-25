## 1. Models, migration, SQLite pragmas (additive only)

- [x] 1.1 Add `TableInventory`, `BackupHistory`, `RestoreHistory`, `RunStatus`, `BackupPartition` models to `store/models.py` (surrogate PK, `cluster_id` FK with `ondelete="CASCADE"`, `UniqueConstraint`/index per table per design.md); remove `Cluster.ops_database` and update the module docstring. Verify: `python -c "from starrocks_br.store import models"` imports cleanly.
- [x] 1.2 Add `event.listens_for(Engine, "connect")` in `store/session.py` issuing `PRAGMA foreign_keys=ON` and `PRAGMA journal_mode=WAL` for SQLite connections. Verify: a quick script opens a session and queries `PRAGMA foreign_keys`/`PRAGMA journal_mode` and gets `1`/`wal`.
- [x] 1.3 Add a new Alembic revision chained after `9bf7a7f90f78_initial_schema.py`: drop `clusters.ops_database`, create the 5 new tables with FKs/uniques/indexes; `downgrade()` reverses this, re-adding `ops_database` with a `server_default` (documented in the migration docstring as a deliberate deviation from true reversal). Verify: `alembic upgrade head` → `alembic downgrade -1` → `alembic upgrade head` on a scratch SQLite file; inspect `.schema` for the 5 tables and absence of `ops_database`.
- [x] 1.4 Fix `tests/test_store_models.py`'s `cluster.ops_database == "ops"` assertion (now-removed column). Verify: `pytest tests/test_store_models.py` passes.
- [x] 1.5 Run full `pytest tests/` to confirm this phase is purely additive and nothing else broke.

## 2. Convert ops-only modules: history, labels, concurrency, inventory_groups

- [x] 2.1 Convert `history.py`'s `log_backup`/`log_restore` to ORM inserts against `BackupHistory`/`RestoreHistory` keyed by `cluster_id`; remove both files' separately-duplicated local `esc()` closures. Update its tests to use a SQLite `Session` fixture. Verify: `pytest tests/` for history's test file passes.
- [x] 2.2 Convert `labels.py`'s `determine_backup_label` to an ORM existence check against `BackupHistory`, keeping the same retry-suffix logic. Update its tests. Verify: `pytest tests/` for labels' test file passes.
- [x] 2.3 Convert `concurrency.py`: `_get_active_jobs_for_scope`/`_insert_new_job`/`_cleanup_stale_job`/`complete_job_slot` become SQLite-only ORM queries against `RunStatus`; `reserve_job_slot` keeps `db` (staleness-check path) and gains `session`/`cluster_id`; normalize `_can_heal_stale_job`'s `db` parameter to first position (all call sites updated in this same task); drop `ops_database` from `_get_user_databases`'s `system_databases` exclusion set. Update its tests. Verify: `pytest tests/` for concurrency's test file passes, including stale-job-healing and conflict-detection cases.
- [x] 2.4 Rewrite `inventory_groups.py` to full ORM queries against `TableInventory` with `(session, cluster_id)` signatures throughout (including `add_memberships_bulk`), dropping `db` and the `_field()` row-normalizer helper entirely. Preserve `add_membership`'s existing check-then-insert semantics (raises `InventoryMembershipConflictError` on conflict). Update its tests. Verify: `pytest tests/` for inventory_groups' test file passes.
- [x] 2.5 Run full `pytest tests/`; if any cross-module import (e.g. from `jobs/handlers.py`, still calling the old signatures) now raises `TypeError` at collection time, add minimal placeholder call-site fixes for just these 4 modules' calls (full `handlers.py` conversion happens in Task Group 5).

## 3. Convert mixed-dependency modules: planner, prune, restore, executor

- [x] 3.1 Convert `planner.py`: `find_latest_full_backup` keeps `db` + gains `session`/`cluster_id`; `find_tables_by_group`/`record_backup_partitions` become SQLite-only; `find_recent_partitions` keeps both; `get_all_partitions_for_tables`/`build_incremental_backup_command` stay unchanged. Update its tests. Verify: `pytest tests/` for planner's test file passes.
- [x] 3.2 Convert `prune.py`: replace `get_successful_backups`'s 3-way manual-SQL join with one SQLAlchemy join query across the ORM models (incidentally fixes the unquoted-interpolation bug for the columns moved into this query); `cleanup_backup_history` becomes SQLite-only ORM delete. Leave `verify_snapshot_exists`/`execute_drop_snapshot` unchanged (still StarRocks-only, same pre-existing unquoted-interpolation bug, explicitly out of scope — do not fix). Update its tests. Verify: `pytest tests/` for prune's test file passes.
- [x] 3.3 Convert `restore.py`: `find_restore_pair`/`get_partitions_from_backup` become SQLite-only; `get_tables_from_backup` keeps `db` (wildcard `SHOW TABLES` branch) + gains `session`/`cluster_id`; `execute_restore`/`execute_restore_flow` keep `db`, gain `session`/`cluster_id`. Update its tests. Verify: `pytest tests/` for restore's test file passes.
- [x] 3.4 Convert `executor.py`: `execute_backup` keeps `db`, gains `session`/`cluster_id` for its `history.log_backup`/`concurrency.complete_job_slot` calls, using short-lived `session_scope()` blocks around just those calls (not spanning the submit/poll loop), per design.md Decision 2. Update its tests. Verify: `pytest tests/` for executor's test file passes.
- [x] 3.5 Confirm `bootstrap_table_inventory`'s logic has moved into `inventory_groups.py` as a plain `(session, cluster_id, ...)` function; do not delete `schema.py` yet (still imported by `jobs/handlers.py` and `cli.py`). Verify: `grep -rn "bootstrap_table_inventory" src/` shows only the new location plus its future callers.
- [x] 3.6 Run full `pytest tests/`.

## 4. Rewrite error_handler.py messages

- [x] 4.1 Delete `_get_ops_database_name`. Rewrite the 9 functions embedding `{ops_db}.<table>` SQL hints (`handle_backup_label_not_found_error`, `handle_no_successful_full_backup_found_error`, `handle_table_not_found_in_backup_error`, `handle_snapshot_not_found_error`, `handle_no_partitions_found_error`, `handle_no_tables_found_error`, `handle_concurrency_conflict_error`, `handle_no_full_backup_found_error`, `handle_invalid_tables_in_inventory_error`) to plain-language descriptions instead of StarRocks SQL that no longer runs against anything. Update matching test string assertions. Verify: `pytest tests/` for error_handler's test file passes.

## 5. Wire jobs/handlers.py (thread_backend.py needs no change)

- [x] 5.1 In `jobs/handlers.py`: remove the `schema` import, delete `_ensure_ready`'s `schema.ensure_ops_schema` call and `OpsSchemaNotInitializedError`. Delete `schema.py` itself in this same commit (last importer now gone). Verify: `grep -rn "import schema" src/` returns nothing; `ls src/starrocks_br/schema.py` fails.
- [x] 5.2 Update each `run_*` handler (`run_backup_full`, `run_backup_incremental`, `run_restore`, `run_prune`) to open short-lived `with session_scope() as session:` blocks around each ops-table touchpoint (pre-flight reads/`reserve_job_slot`, and post-poll `history.log_*`/`concurrency.complete_job_slot`), passing `session, cluster.id` into the Task Group 2/3-converted functions — no new `session` parameter on the handler signatures themselves, and no session held across the StarRocks submit/poll loop. Verify: existing handler-level tests updated and passing with a real or fake SQLite `Session`.
- [x] 5.3 Confirm `jobs/thread_backend.py::_run_job` needs no code change (its existing `expunge()` pattern already matches the chosen design). Verify: re-read the file and confirm no session is passed into `JOB_HANDLERS[job_type](...)`.
- [x] 5.4 Run full `pytest tests/`, including a case that exercises `on_progress` firing mid-handler (its own independent `session_scope()` call) to confirm no session/connection conflict.

## 6. FastAPI routes + schemas

- [x] 6.1 Simplify all 6 routes in `api/routes/inventory_groups.py`: drop `connect_or_503`/`database.close()`, call `inventory_groups.*(db, cluster_id, ...)` directly; keep `get_cluster_or_404` where still needed for 404 checks/error messages. Verify: `pytest tests/` for this route file passes.
- [x] 6.2 Update `api/routes/jobs.py::_submit_backup_job`'s `group_exists` pre-check to a pure SQLite call, dropping `connect_or_503`/`database.close()` from this path. Verify: `pytest tests/` for this route file passes.
- [x] 6.3 Remove `ops_database=payload.ops_database` from `api/routes/clusters.py::create_cluster`. Verify: `pytest tests/` for this route file passes.
- [x] 6.4 Remove `ops_database` from `ClusterCreate`/`ClusterUpdate`/`ClusterRead` in `api/schemas.py`. Check whether the base model config uses `extra="forbid"` and document precisely whether a stray `ops_database` in a request body is ignored or rejected. Verify: a test posting `ops_database` in the body confirms the actual resulting behavior; `pytest tests/` for schemas passes.

## 7. cli_api/cluster.py flag removal

- [x] 7.1 Remove the `--ops-database` click option and its JSON body entry in `cli_api/cluster.py`. Update any CLI arg-parsing/`--help`-snapshot tests. Verify: `<cli-entrypoint> cluster create --help` shows no `--ops-database` flag; `pytest tests/` passes.

## 8. Legacy cli.py identity resolution

- [x] 8.1 Add `get_cluster_identity(config)` to `config.py` (returns `config["name"]` if present, else `f"{host}:{port}/{database}"`); remove `get_ops_database` once `cli.py` no longer calls it. Verify: unit test for both branches of `get_cluster_identity`.
- [x] 8.2 Add `resolve_cluster(session, cfg)` to `cli.py`: get-or-create a `Cluster` row by derived identity (stored in `Cluster.name`), refreshing mutable connection fields from YAML each run without overwriting the stored identity unless `config["name"]` is explicitly present and differs. Verify: a test showing a second `init` with the same YAML reuses the same row and refreshes fields.
- [x] 8.3 Make `init` the only command that creates the `Cluster` row (plus bootstraps `table_inventory`); make `backup-full`/`backup-incremental`/`restore`/`prune` require the row to already exist, failing with a clear "run init first" error otherwise. Verify: a test running a non-init command against a fresh config fails with that error rather than auto-creating a row.
- [x] 8.4 Thread every remaining core-library call in `cli.py` through the new `(session, cluster.id, ...)` convention from Task Groups 2–3; open one `session_scope()` per CLI invocation wrapping `resolve_cluster` and the command's logic. Verify: `pytest tests/` for `cli.py`'s test files passes.
- [x] 8.5 Document (README/CLI `--help` epilogue or first-run message) that `alembic upgrade head` against `STARROCKS_BR_DATABASE_URL` is now a prerequisite for the standalone CLI. Verify: the message appears in `--help` output or on first run without a migrated database.

## 9. Test suite cleanup

- [x] 9.1 Add uniqueness tests to `tests/test_store_models.py` for each new table's `UniqueConstraint` (`TableInventory`, `BackupHistory`, `RestoreHistory`, `RunStatus`, `BackupPartition`). Verify: each test asserts `IntegrityError` on a duplicate-key insert.
- [x] 9.2 Add a cascade-delete test to `tests/test_store_models.py`: create a `Cluster` with one row in each of the 5 new tables, delete the `Cluster`, assert all 5 tables' rows for that `cluster_id` are gone. Verify: test passes (validates Phase 1's `PRAGMA foreign_keys=ON` wiring — if it fails, suspect the pragma hook first).
- [x] 9.3 Delete `tests/test_schema_setup.py` (entirely about the now-deleted `schema.py` DDL builders). Verify: `pytest tests/` full run is green with the file gone.

## 10. Docs + CHANGELOG

- [x] 10.1 Update `docs/configuration.md` and `docs/api.md` to remove/update all `ops_database` references. Verify: `grep -ri ops_database docs/` returns nothing.
- [x] 10.2 Add a `CHANGELOG.md` entry describing the breaking change (ops bookkeeping now SQLite-backed by `cluster_id`; `ops_database` removed from API/CLI/YAML; `alembic upgrade head` now required for both code paths). Verify: entry present and reviewed.
- [x] 10.3 Final repo-wide check: `grep -ri ops_database src/ docs/` returns nothing outside the new Alembic migration file. Run full `pytest tests/` as the final gate.
- [x] 10.4 Manually verify end-to-end against a real/local StarRocks instance (per design.md's Migration Plan): via the API, create a cluster → inventory group → full backup → restore; confirm no `ops` database is ever created on StarRocks and SQLite rows exist scoped by `cluster_id`. Repeat via the legacy CLI against the same StarRocks instance and `STARROCKS_BR_DATABASE_URL`, confirming rows land in the same shared tables.
