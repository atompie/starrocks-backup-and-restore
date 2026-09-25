## Why

`GET /cluster/2/inventory-groups` failed with `Unknown database 'ops'` because the tool stores its own backup/restore bookkeeping (`table_inventory`, `backup_history`, `restore_history`, `run_status`, `backup_partitions`) inside a database on the target StarRocks cluster itself, named via the user-supplied `ops_database` field (default `"ops"`). This is unsafe — if the StarRocks cluster is down or corrupted, exactly when you need to restore, the metadata telling you what backups exist is unreachable too — and it leaks an internal implementation detail (`ops_database`) into the API, CLI, and YAML config for no user benefit.

## What Changes

- Move all 5 ops bookkeeping tables from a per-cluster StarRocks database into the app's own SQLite metastore (already used for `Cluster`/`Job`/`Schedule`, Alembic-migrated), keyed by `cluster_id`.
- **BREAKING**: Remove `ops_database` entirely from the user-facing surface — `ClusterCreate`/`ClusterUpdate`/`ClusterRead` API schemas, the `--ops-database` CLI flag, and the YAML `ops_database` config key.
- **BREAKING**: `alembic upgrade head` against `STARROCKS_BR_DATABASE_URL` becomes a hard prerequisite for both the FastAPI server and the standalone `cli.py` tool — replacing today's auto-create-on-first-use StarRocks schema initialization.
- Both code paths are in scope: the FastAPI/SQLite-backed API + `cli_api` client, and the legacy standalone `cli.py`/YAML-config tool (which gains its own SQLite-backed cluster-identity resolution, since it has none today).
- No production data exists yet — this is a clean cutover, not a data migration. The old StarRocks-side `ops` schema code (`schema.py`) is deleted, not dual-supported.
- Incidentally fixes a pre-existing SQL-injection-shaped bug in `prune.py`'s ops-table queries (unquoted string interpolation) as a side effect of the ORM conversion; the two StarRocks-only functions in that file that still have the same pattern are left unchanged (out of scope).
- Session-handling in job execution is designed for future concurrency: ops-table access inside job handlers uses short-lived sessions scoped to each individual read/write rather than one session held open for a job's full (multi-minute) duration, so a future non-thread-based job backend (e.g. a Kafka-consumer worker) running many jobs concurrently won't have long-held SQLite write transactions serializing against each other.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `api-inventory-groups`: inventory group listings and lookups no longer reflect "the cluster's own `table_inventory` state live at request time" against StarRocks — they read the app's own SQLite-backed, `cluster_id`-scoped store instead. Functionally equivalent (the SQLite copy is the sole source of truth going forward, not a stale cache of a StarRocks-side table), but the requirement wording naming StarRocks as the location of record needs updating.
- `api-job-execution`: the outcome-parity requirement ("the same StarRocks-side outcome... including ops schema records") is updated since ops bookkeeping records no longer live on the StarRocks side at all — parity is now stated in terms of the SQLite-backed bookkeeping rows produced, not a StarRocks schema.

## Impact

- **Affected code**: `store/models.py`, `store/session.py`, a new Alembic migration, `schema.py` (deleted), `inventory_groups.py`, `planner.py`, `concurrency.py`, `history.py`, `restore.py`, `prune.py`, `labels.py`, `executor.py`, `error_handler.py`, `jobs/handlers.py`, `jobs/thread_backend.py`, `api/routes/{inventory_groups,jobs,clusters}.py`, `api/schemas.py`, `cli.py`, `config.py`, `cli_api/cluster.py`.
- **Affected APIs**: `POST /cluster`, `PATCH /cluster/{id}`, `GET /cluster/{id}` drop `ops_database`; inventory-group and job-submission routes no longer open a StarRocks connection for ops-table access.
- **Affected users**: anyone currently passing `ops_database`/`--ops-database`/YAML `ops_database` must drop it; anyone running the standalone CLI or API server for the first time must run `alembic upgrade head` first (previously zero setup beyond a YAML file or a running API server).
- **Dependencies**: none new; uses the existing SQLAlchemy/Alembic stack already in place for `Cluster`/`Job`/`Schedule`.
