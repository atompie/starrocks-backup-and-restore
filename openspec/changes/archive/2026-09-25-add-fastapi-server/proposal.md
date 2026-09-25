## Why

Today the tool is operated exclusively as a single-process CLI: every command loads one `config.yaml` describing exactly one StarRocks cluster/database/repository, and runs synchronously to completion (or up to 24h of blocking polling) in the invoking process. There is no way to register additional clusters, trigger or monitor backups remotely, or observe progress without shelling into the machine and querying `ops.backup_history` directly. Scheduling is entirely external (user-maintained cron/K8s CronJob entries per group), with no visibility or central control. To operate this tool as a service — registering new StarRocks clusters/databases, triggering and monitoring backups and restores remotely, and centrally managing schedules — the project needs a FastAPI server alongside the existing CLI, backed by an architecture that can grow into distributed job execution (Kafka/Redis-backed workers) without a rewrite.

## What Changes

- Add a new FastAPI server exposing cluster registration, backup/restore/prune job submission, job status/progress polling, and schedule management over HTTP, protected by a shared bearer-token API key.
- Introduce a persistent metadata store for the API (SQLAlchemy models: `Cluster`, `Job`, `Schedule`), defaulting to an embedded SQLite file, with Alembic migrations so it can later point at MySQL/Postgres via `DATABASE_URL` with no model changes.
- Introduce a pluggable job execution layer: a `JobBackend` registry (ships with an in-process `thread` backend) and a backend-agnostic `JOB_HANDLERS` map that reuses the existing core library (`db.py`, `planner.py`, `executor.py`, `restore.py`, `prune.py`, `schema.py`, `repository.py`, ...) unchanged. Backend is chosen per-request with a configured default, so Kafka/Redis-backed backends can be added later as new `JobBackend` implementations without touching API routes or handlers.
- Extend the existing backup/restore polling loops (`executor.poll_backup_status`, `restore.poll_restore_status`) with an optional `on_progress` callback so job progress (StarRocks' native `Progress`/state fields when available, otherwise just started/running/finished) can be persisted incrementally into the `Job` row and exposed via `GET /jobs/{id}`. The CLI continues to call these functions without a callback, so its behavior is unchanged.
- Add schedule management: a `Schedule` model (cluster, job type, group, cadence, backend override, enabled) with CRUD endpoints, and a `POST /schedules/run-due` endpoint that finds due schedules and submits jobs through the same `JobBackend` registry.
- Add new CLI subcommands that act as thin HTTP clients against the API (cluster registration, job submission/status, and `schedule run-due` for cron/K8s CronJob invocation) — separate from and additive to the existing direct-to-StarRocks CLI commands, which are unchanged.
- **BREAKING**: none. All existing CLI commands, `config.yaml` format, and direct-to-StarRocks behavior remain unchanged; the API and its metadata store are new, additive surfaces.

## Capabilities

### New Capabilities
- `api-authentication`: shared bearer-token/API-key protection for all API endpoints.
- `api-cluster-registry`: register, list, update, and remove StarRocks clusters (connection details + repository) via the API, persisted in the SQLAlchemy metadata store.
- `api-job-execution`: submit backup/restore/prune operations against a registered cluster as asynchronous jobs; poll job status and progress; pluggable execution backend selection (thread now, extensible later).
- `api-scheduling`: define, list, update, and remove recurring backup schedules per cluster/group; trigger due schedules on demand (for cron/K8s CronJob-driven invocation).
- `cli-api-client`: new CLI subcommands that operate against the running API server over HTTP (cluster management, job submission/status, schedule run-due), independent of the existing direct-to-StarRocks CLI commands.

### Modified Capabilities
(none — no existing specs exist yet for this project; all behavior above is additive to the current, unspecified CLI implementation)

## Impact

- **New code**: `src/starrocks_br/api/` (FastAPI app, routes, auth), `src/starrocks_br/store/` (SQLAlchemy models, session, Alembic migrations), `src/starrocks_br/jobs/` (JobBackend registry, thread backend, JOB_HANDLERS), new CLI command group(s) for the HTTP-client subcommands.
- **Modified code**: `executor.py` and `restore.py` gain an optional `on_progress` parameter on their poll functions (backward compatible, default `None`); `pyproject.toml` gains new dependencies (`fastapi`, `uvicorn`, `sqlalchemy`, `alembic`, and an HTTP client library for the new CLI commands, e.g. `httpx`).
- **Unaffected**: existing CLI commands (`init`, `backup full/incremental`, `restore`, `prune`), `config.yaml` schema, and the per-cluster `ops` schema (backup_history, table_inventory, run_status, backup_partitions) — these keep working exactly as today.
- **New operational surface**: a long-running API server process, an API key/secret to provision, and a new metadata database file (or connection) to back up/operate.
