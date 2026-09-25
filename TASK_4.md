# TASK 4: Move ops bookkeeping tables from StarRocks into the SQLite metastore, and remove `ops_database` from the user-facing surface

## Context

`GET /cluster/2/inventory-groups` failed with `Unknown database 'ops'` because the tool stores its own backup/restore bookkeeping (`table_inventory`, `backup_history`, `restore_history`, `run_status`, `backup_partitions`) **inside a database on the target StarRocks cluster itself**, named via the user-supplied `ops_database` field (default `"ops"`). That design has two problems:

1. **Safety**: if the StarRocks cluster is down or corrupted — exactly the situation where you need to restore — the metadata telling you what backups exist and how to restore them is unreachable too.
2. **Leaky interface**: `ops_database` is an internal implementation detail that users currently have to know about and type (API field, CLI flag, YAML config key), for no benefit to them.

The fix: move all 5 bookkeeping tables into the app's own SQLite metastore (already used for `Cluster`/`Job`/`Schedule`, Alembic-migrated), keyed by `cluster_id` instead of a per-cluster database name. This solves both problems at once — the bookkeeping survives a dead StarRocks cluster, and there's no longer any "ops database name" concept to expose to users at all.

**Decisions already confirmed with the user:**
- Both code paths are in scope: the FastAPI/SQLite-backed API + `cli_api` client, **and** the legacy standalone `cli.py`/YAML-config tool (which currently talks to StarRocks directly with no SQLite involvement).
- No production data exists yet — this is a clean cutover, not a data migration. The old StarRocks-side `ops` schema code is deleted, not dual-supported.
- All 5 tables move, including `run_status` (the job-concurrency lock table), for one consistent storage location.

## Design

### 1. New SQLAlchemy models (`src/starrocks_br/store/models.py`)

Add 5 new tables alongside the existing `Cluster`/`Job`/`Schedule`, each with a surrogate integer PK (matching house style) and a `cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False, index=True)`:

- **`TableInventory`**: `inventory_group`, `database_name`, `table_name` (String), `created_at`, `updated_at`. `UniqueConstraint(cluster_id, inventory_group, database_name, table_name)` — mirrors the current StarRocks `UNIQUE KEY`. Index on `(cluster_id, inventory_group)`.
- **`BackupHistory`**: `label`, `backup_type`, `status`, `repository` (String), `started_at`, `finished_at`, `error_message`, `created_at`. `UniqueConstraint(cluster_id, label)`.
- **`RestoreHistory`**: `job_id`, `backup_label`, `restore_type`, `status`, `repository`, `started_at`, `finished_at`, `error_message`, `verification_checksum`, `created_at`. `UniqueConstraint(cluster_id, job_id)`.
- **`RunStatus`**: `scope`, `label`, `state` (default `"ACTIVE"`), `started_at`, `finished_at`. `UniqueConstraint(cluster_id, scope, label)`; index on `(cluster_id, scope, state)` for the active-job lookup.
- **`BackupPartition`**: `key_hash` (keep the existing MD5 computation for continuity), `label`, `database_name`, `table_name`, `partition_name`, `created_at`. `UniqueConstraint(cluster_id, key_hash)`; index on `(cluster_id, label)`.

Remove `Cluster.ops_database` entirely (`store/models.py:65`).

For `table_inventory`'s upsert-like StarRocks semantics: keep the existing check-then-insert pattern already used in `inventory_groups.add_membership` (raises `InventoryMembershipConflictError` on conflict) — the new `UniqueConstraint` is just a backstop, not a behavior change.

**Cascade delete**: enable `PRAGMA foreign_keys=ON` for SQLite connections in `store/session.py` (via an `event.listens_for(Engine, "connect")` hook) so `ondelete="CASCADE"` actually cleans up the 5 tables when a `Cluster` row is deleted — today `delete_cluster` doesn't need to worry about this because the old data lived on StarRocks.

### 2. Alembic migration

One new revision (chained after `9bf7a7f90f78_initial_schema.py`): drop `clusters.ops_database`, create the 5 tables with their FKs/uniques/indexes as above. `downgrade()` reverses this (drop the 5 tables, re-add `ops_database` with a server default for schema symmetry — no real data to restore).

### 3. Core library module conversion

General rule: a function that *only* touches the 5 ops tables changes from `f(db, ..., ops_database="ops")` to `f(session: Session, cluster_id: int, ...)`, dropping the StarRocks connection entirely. A function that mixes ops-table access with a live StarRocks call (`SHOW TABLES`, `SHOW PARTITIONS`, `SHOW BACKUP`, etc.) keeps `db` **and** gains `session`/`cluster_id`.

- **`schema.py`** — deleted. Its DDL builders (`initialize_ops_schema`, `ensure_ops_schema`, `get_*_schema`) have no SQLite equivalent (Alembic now owns schema creation). `bootstrap_table_inventory`'s real logic moves into `inventory_groups.py`.
- **`inventory_groups.py`** — full rewrite to ORM queries against `TableInventory`, `(session, cluster_id)` signatures, no `db` needed. Drop the `_field()` raw-tuple helper.
- **`planner.py`** — split by function: `find_latest_full_backup` needs both `db` (for `db.timezone`) and `session`/`cluster_id`; `find_tables_by_group`, `record_backup_partitions` become SQLite-only; `find_recent_partitions` keeps both (SQLite reads + live `SHOW TABLES`/`SHOW PARTITIONS`); `get_all_partitions_for_tables` and `build_incremental_backup_command` are unchanged (no ops-table touch).
- **`concurrency.py`** — `_get_active_jobs_for_scope`/`_insert_new_job`/`_cleanup_stale_job`/`complete_job_slot` become SQLite-only; `reserve_job_slot`'s staleness-check path keeps `db`. Drop `ops_database` from the `system_databases` exclusion set (no longer a StarRocks-side database to exclude).
- **`history.py`** — pure SQLite, `db` dropped, ORM inserts replace the manual SQL-escaping (`esc()` helper removed).
- **`restore.py`** — `find_restore_pair`, `get_partitions_from_backup` become SQLite-only; `get_tables_from_backup` keeps `db` too (its `'*'`-wildcard branch calls `SHOW TABLES`); `execute_restore`/`execute_restore_flow` keep `db` and gain `session`/`cluster_id`.
- **`prune.py`** — `get_successful_backups`'s 3-way join becomes a single SQLAlchemy query joining the 3 tables on `cluster_id` (this also incidentally fixes prune.py's pre-existing unquoted `repository`/`group` interpolation, as a side effect of parameterized ORM queries, not extra scope). `cleanup_backup_history` becomes SQLite-only. `verify_snapshot_exists`/`execute_drop_snapshot` unchanged.
- **`labels.py`** — `determine_backup_label` becomes SQLite-only, same retry-suffix logic.
- **`executor.py`** — `execute_backup` keeps `db` (submit/poll) and gains `session`/`cluster_id` for its `history.log_backup`/`concurrency.complete_job_slot` calls.
- **`error_handler.py`** — delete `_get_ops_database_name`. **User-facing copy change**: ~9 error-handler functions currently print SQL hints like `SELECT ... FROM {ops_db}.backup_history` — these must be rewritten (plain-language description of the SQLite-backed history, or a pointer to a new introspection command) since that SQL no longer runs against anything that exists.

### 4. Wiring a session alongside the StarRocks connection

- **`jobs/handlers.py`**: every `run_*` handler needs a `Session` for the job's duration, not just the StarRocks connection. Change handler signatures to accept `session: Session` alongside `cluster: Cluster` (use `cluster.id` for `cluster_id`, avoid a second redundant parameter). Locate and update whatever invokes `JOB_HANDLERS[job_type](...)` (the job backend runner) to open a `session_scope()` per job and pass it through. `_ensure_ready` drops its `schema.ensure_ops_schema` call and the `OpsSchemaNotInitializedError` path entirely.
- **`api/routes/inventory_groups.py`**: these routes no longer need a StarRocks connection at all for group CRUD — drop `connect_or_503`/`database.close()` scaffolding, call `inventory_groups.*` directly with the already-injected SQLite `Session` and `cluster.id`.
- **`api/routes/jobs.py`**: same simplification for the `group_exists` pre-check in `_submit_backup_job`.
- **`api/routes/clusters.py`**: drop `ops_database=payload.ops_database` from `create_cluster`.

### 5. Legacy CLI identity resolution (`cli.py` + `config.py`)

The legacy YAML-driven CLI has no `id`/`name` concept today. Resolve this with **no new required config field**:

- `get_cluster_identity(config)`: returns `config["name"]` if the (new, optional) YAML field is present, else derives `f"{host}:{port}/{database}"` from fields that are already required.
- New `resolve_cluster(session, cfg)`: get-or-create a `Cluster` row by that identity, refreshing mutable fields (host/port/user/password/repository) from the YAML on each run so the config file stays authoritative.
- **Semantics**: `init` is required and creates the `Cluster` row (plus bootstraps `table_inventory`); every other command (`backup-full`, `backup-incremental`, `restore`, `prune`) requires the row to already exist and fails with a "run init first" error if not — this preserves today's stricter "must init first" behavior rather than silently auto-registering clusters from any command (safer against `--config` typos).
- Document that `alembic upgrade head` against `STARROCKS_BR_DATABASE_URL` is now a prerequisite for the standalone CLI too, since it previously required zero setup beyond a YAML file.

### 6. Remove `ops_database` from the user-facing surface

- `api/schemas.py` — remove the field from `ClusterCreate`, `ClusterUpdate`, `ClusterRead`.
- `cli_api/cluster.py` — remove the `--ops-database` flag and its wiring into the `POST /cluster` payload.
- `config.py` — remove `get_ops_database`.
- `docs/configuration.md`, `docs/api.md`, `CHANGELOG.md` — remove/update references (add a new CHANGELOG entry describing the breaking change rather than editing history).
- Do **not** edit the archived `openspec/changes/archive/2026-09-25-add-inventory-group-api/tasks.md` — its "ops_database is trusted config" note becomes historically superseded, not incorrect-in-place; a new OpenSpec change proposal for this migration (optional, via the `openspec-propose` skill) can note the supersession.

## Rollout order

1. Models + Alembic migration + SQLite FK-cascade pragma (purely additive, nothing references the new tables yet).
2. Convert `history.py`, `labels.py`, `concurrency.py`, `inventory_groups.py` (simplest, least cross-coupling) — update each module's tests in lockstep, not in one batch at the end.
3. Convert `planner.py`, `prune.py`, `restore.py`, `executor.py` (mixed-dependency modules) — delete `schema.py` once nothing imports it.
4. `error_handler.py` message rewrites.
5. `jobs/handlers.py` + its caller (the job backend runner) — wire `session` through.
6. FastAPI routes (`inventory_groups.py`, `jobs.py`, `clusters.py`) + `api/schemas.py` together.
7. `cli_api/cluster.py` flag removal.
8. Legacy `cli.py` + `config.py` — identity resolution, `init`-creates/others-require semantics.
9. `tests/test_store_models.py` and `tests/test_schema_setup.py` (the latter likely needs deleting/rewriting to test the Alembic migration's resulting schema instead of the deleted DDL builders).
10. Docs + CHANGELOG.

## Critical files

- `src/starrocks_br/store/models.py`, `src/starrocks_br/store/session.py`
- `src/starrocks_br/store/migrations/versions/9bf7a7f90f78_initial_schema.py` (style reference for the new revision)
- `src/starrocks_br/schema.py` (deleted), `src/starrocks_br/inventory_groups.py`
- `src/starrocks_br/planner.py`, `src/starrocks_br/concurrency.py`, `src/starrocks_br/history.py`, `src/starrocks_br/restore.py`, `src/starrocks_br/prune.py`, `src/starrocks_br/labels.py`, `src/starrocks_br/executor.py`, `src/starrocks_br/error_handler.py`
- `src/starrocks_br/jobs/handlers.py` and its caller (job backend runner — to be located)
- `src/starrocks_br/api/routes/{inventory_groups,jobs,clusters}.py`, `src/starrocks_br/api/schemas.py`
- `src/starrocks_br/cli.py`, `src/starrocks_br/config.py`, `src/starrocks_br/cli_api/cluster.py`

## Verification

1. Run `pytest tests/` after each module conversion step — keep it green throughout, not just at the end.
2. Migration round-trip: `alembic upgrade head` → `alembic downgrade -1` → `alembic upgrade head` against a scratch SQLite file; inspect `.schema` to confirm the 5 new tables and the dropped `ops_database` column.
3. End-to-end cycle against a real/local StarRocks instance: via the API, create a cluster, an inventory group, run a full backup, then a restore, to completion. Confirm via `SHOW DATABASES` on StarRocks that no `ops` database was ever created, and inspect the SQLite file directly to confirm the bookkeeping rows exist, scoped by `cluster_id`. Repeat via the legacy CLI against the same StarRocks instance and same `STARROCKS_BR_DATABASE_URL`, confirming its rows land in the same shared tables.
4. Confirm `ops_database` is gone from the user surface: `POST /cluster` with `ops_database` in the body is rejected or ignored and never echoed back; `--help` output for both CLIs shows no `--ops-database` flag; `grep -ri ops_database src/ docs/` returns nothing outside the Alembic migration file.
5. Regression-test `concurrency.reserve_job_slot`'s conflict-detection and stale-job-healing logic specifically (not just happy-path CRUD) — `run_status` is the one table with real lock semantics, the highest-risk area for subtle behavior change.
