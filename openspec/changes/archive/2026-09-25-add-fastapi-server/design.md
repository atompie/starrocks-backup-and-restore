## Context

See `proposal.md` - Why/What Changes for motivation. Relevant current-state constraints:

- The core library (`db.py`, `config.py`, `schema.py`, `planner.py`, `executor.py`, `restore.py`, `prune.py`, `repository.py`, `labels.py`, `health.py`, `concurrency.py`, `history.py`, `timezone.py`) is already decoupled from the CLI: every function takes an explicit `StarRocksDB` connection and plain arguments, and knows nothing about Click, HTTP, or threads. This is the foundation the API builds on.
- `executor.poll_backup_status` / `restore.poll_restore_status` currently block synchronously (up to `MAX_POLLS = 86400` iterations) and only extract `SnapshotName`/`State` from `SHOW BACKUP`/`SHOW RESTORE` rows, discarding any other columns StarRocks returns.
- Per-cluster operational bookkeeping (`ops.backup_history`, `ops.table_inventory`, `ops.run_status`, `ops.backup_partitions`) lives inside each target StarRocks cluster today and is untouched by this change - it is orthogonal to the new API-owned metadata store.
- There is currently no persistent process; every CLI invocation is a fresh, short-lived process reading one `config.yaml`.

## Goals / Non-Goals

**Goals:**
- Introduce a FastAPI server and its metadata store without changing any existing CLI command's behavior, config format, or the per-cluster `ops` schema.
- Make the job-execution seam pluggable from day one (`JobBackend` interface + registry) so a Kafka- or Redis-backed backend can be added later as a new class, not a rewrite.
- Make the metadata store portable from SQLite to MySQL/Postgres via `DATABASE_URL` alone, by using SQLAlchemy models and Alembic migrations from the start.
- Report real progress when StarRocks provides it, degrading gracefully to started/running/finished when it doesn't, without the core polling functions knowing anything about jobs, HTTP, or persistence.

**Non-Goals:**
- Multi-user accounts, RBAC, or per-user credentials for the API (single shared API key only, per `api-authentication` spec).
- Actually implementing a Kafka or Redis execution backend in this change - only the interface and the in-process `thread` backend ship now.
- High-availability / multi-replica API deployment (SQLite is single-writer-friendly; that's an explicit trade-off, see Risks).
- Changing how `ops.*` bookkeeping works inside each target cluster.

## Decisions

### 1. Module layout
New top-level packages under `src/starrocks_br/`:

```
src/starrocks_br/
  api/            # FastAPI app: routes, auth dependency, request/response schemas
    app.py
    auth.py
    routes/
      clusters.py
      jobs.py
      schedules.py
      health.py
  store/          # SQLAlchemy models, session/engine factory, Alembic env
    models.py     # Cluster, Job, Schedule
    session.py
    crypto.py     # password-at-rest encryption for Cluster.password
    migrations/   # Alembic
  jobs/           # Execution backend abstraction, ships with "thread" only
    backend.py    # JobBackend protocol, registry
    thread_backend.py
    handlers.py   # JOB_HANDLERS: job_type -> callable(cluster, params, on_progress) -> result
  cli_api/        # New CLI subcommands (HTTP client), separate module from cli.py
    cluster.py
    job.py
    schedule.py
```
The existing `cli.py` and core modules are untouched except for the `on_progress` addition (Decision 4). `cli_api/` commands are registered as a new Click group, additive to the existing `cli` group in `cli.py` (e.g. `starrocks-br api cluster add ...`), so `starrocks-br --help` shows both direct-to-StarRocks commands and API-client commands without ambiguity.

**Alternative considered**: put API code inside `cli.py`'s package flatly. Rejected - would blur the "independent front-ends, shared core" boundary agreed in exploration and make it harder to keep the API optional (e.g. installable as an extra) later.

### 2. Metadata store: SQLAlchemy + Alembic, SQLite default
`store/models.py` defines three tables:
- `clusters`: id, name (unique), host, port, user, password_encrypted, repository, default_backend, created_at, updated_at.
- `jobs`: id, cluster_id (FK), job_type (backup_full/backup_incremental/restore/prune), params_json, backend, status (PENDING/RUNNING/SUCCESS/FAILED), progress_pct (nullable), state_detail (nullable string, e.g. "UPLOADING"), error_message (nullable), created_at, started_at (nullable), finished_at (nullable).
- `schedules`: id, cluster_id (FK), job_type, group_name, cadence (cron expression string), backend (nullable override), enabled (bool), next_run_at, last_run_job_id (nullable FK), created_at, updated_at.

`DATABASE_URL` env var selects the engine (`sqlite:///./starrocks_br_api.db` default). Alembic migrations are authored against SQLAlchemy Core types compatible with both SQLite and MySQL (no SQLite-only types, explicit `String` lengths for MySQL compatibility). `Cluster.password_encrypted` uses symmetric encryption (`store/crypto.py`) keyed by a server-side secret (separate env var from the API bearer key), not the SQLAlchemy layer itself - this keeps the encryption swappable independent of the DB engine.

**Alternative considered**: raw SQL against SQLite (matching the existing `db.py` style). Rejected per explicit ask - SQLAlchemy is required specifically for the MySQL portability path.

### 3. Job execution: `JobBackend` protocol + registry, `JOB_HANDLERS` map
```python
class JobBackend(Protocol):
    name: str
    def enqueue(self, job_id: int) -> None: ...
```
`jobs/handlers.py` maps `job_type -> Callable[[Cluster, dict, Callable[[dict], None]], dict]`, each handler a thin wrapper that builds a `StarRocksDB` from the `Cluster` row and calls the existing core functions (`planner`, `executor.execute_backup`, `restore.execute_restore_flow`, `prune.*`) exactly as `cli.py` does today, passing an `on_progress` callback (Decision 4).

`ThreadBackend.enqueue(job_id)` looks up the job, resolves the handler by `job_type`, and runs it in a `concurrent.futures.ThreadPoolExecutor`, updating the `jobs` row (via a new SQLAlchemy session) to RUNNING before invoking the handler and to SUCCESS/FAILED after. A backend registry (`jobs/backend.py`) maps backend name -> instance, built at server startup from an `enabled_backends` config list (default: `["thread"]`) plus a `default_backend` config value. The job-submission API route resolves the backend name (`request.backend or cluster.default_backend or server.default_backend`), validates it against the registry, creates the `Job` row, and calls `registry[name].enqueue(job.id)`.

Adding a future backend means: implement `JobBackend`, register it by name, and (for out-of-process backends like Kafka) ship a separate worker entrypoint that consumes messages and calls the same `jobs/handlers.py` functions - no change to routes, models, or existing handlers.

**Alternative considered**: `asyncio` tasks instead of a thread pool. Rejected - the core library's DB calls (`mysql.connector`) are blocking; running them on the event loop would stall the whole API process. A thread pool keeps FastAPI's event loop free while reusing synchronous core code unchanged.

### 4. Progress reporting via an optional `on_progress` callback
`executor.poll_backup_status` and `restore.poll_restore_status` gain an optional keyword parameter `on_progress: Callable[[dict], None] | None = None`, called on each poll iteration with `{"state": ..., "progress_pct": <int|None>, "raw": {...}}`, parsed from whatever `SHOW BACKUP`/`SHOW RESTORE` returns that poll (StarRocks' `Progress`/`UnfinishedTasks` columns when present, `None` otherwise). Default `None` preserves exact current behavior for the CLI (which never passes it). `jobs/handlers.py` supplies a callback that writes `progress_pct`/`state_detail` onto the `Job` row via SQLAlchemy on every invocation (cheap for SQLite at typical poll intervals of seconds).

Because `SHOW BACKUP`/`SHOW RESTORE` column availability may vary by StarRocks version, the parser SHALL treat a missing/unparseable progress field as `None` rather than raising - the job then reports status/state without a percentage, satisfying "if there is no way to show progress, just show it started/ended."

**Alternative considered**: have the API poll `SHOW BACKUP` independently of the handler's own poll loop. Rejected - would duplicate polling against the live cluster and could race with the handler's own `SnapshotName` tracking/loss-detection logic in `poll_backup_status`.

### 5. Scheduling: `POST /schedules/run-due` triggers via the existing job-submission path
`api/routes/schedules.py` implements `run_due()`: within one DB transaction, select schedules where `enabled=true AND next_run_at <= now()`, and for each, atomically advance `next_run_at` to the next occurrence *before* calling the same internal `submit_job(cluster, job_type, params, backend)` function the direct job-submission route uses - the row-level update-then-submit ordering (with the DB transaction as the concurrency boundary) is what makes a second concurrent `run-due` call see an already-advanced `next_run_at` and skip re-triggering (satisfies the idempotency scenario in `api-scheduling`).

`cli_api/schedule.py` provides `starrocks-br api schedule run-due --api-url ... --api-key ...`, a thin HTTP client calling that endpoint once and exiting - intended to be invoked by external cron/Kubernetes CronJob on a short interval (e.g. every minute), per the "CLI calls the API over HTTP" decision from exploration. Because job execution happens inside the long-lived API process (via whichever `JobBackend` is configured), the short-lived cron-invoked CLI process does not itself need to stay alive for the job's duration - it only needs to succeed in triggering it.

### 6. Auth
A FastAPI dependency (`api/auth.py`) checks `Authorization: Bearer <token>` against an env-configured API key on every route except `/health`. No session/user state.

## Risks / Trade-offs

- [SQLite is single-writer] -> Acceptable for the in-process `thread` backend (one process). A future out-of-process backend (Kafka/Redis worker as a separate process) will need concurrent writers to the `jobs` table from both the API process and worker processes; this is exactly the trigger point documented in the proposal for switching `DATABASE_URL` to MySQL/Postgres - no code change required when that happens, since models are SQLAlchemy already.
- [Progress fidelity depends on StarRocks version] -> Parser treats unknown/missing progress fields as `None` and the API still reports state + timestamps; verify actual `SHOW BACKUP`/`SHOW RESTORE` columns against the target StarRocks version during implementation (could not be verified against the available test instance, which has no repository/backups configured).
- [Cluster password at rest] -> Encrypted via `store/crypto.py` with a server-side key separate from the API bearer key; losing that key makes stored passwords unrecoverable (must be re-entered via update), which is an acceptable trade-off versus storing plaintext.
- [Thread backend and long backups share the API process] -> A crash/restart of the API process loses in-flight thread-backend jobs' liveness (the `Job` row would be left RUNNING with no process updating it). Out of scope to fix in this change; a future backend or a reconciliation sweep (mark stale RUNNING jobs as unknown/failed on startup) is a natural follow-up, not required for the spec as written.
- [Two CLIs in one binary] -> `starrocks-br backup full` (direct) and `starrocks-br api job submit backup-full` (HTTP) look similar but behave differently (sync vs async, needs API server vs needs StarRocks access). Mitigated by nesting all HTTP-client commands under a distinct `api` subcommand group and documenting the split clearly (task in tasks.md).

## Migration Plan

1. Ship additively: new packages/tables/dependencies only; no changes to `config.yaml` schema or existing CLI command behavior. Existing users are unaffected until they choose to run the new server.
2. First deploy: operator sets `STARROCKS_BR_API_KEY`, `STARROCKS_BR_DB_ENCRYPTION_KEY`, optionally `DATABASE_URL` (defaults to local SQLite file), starts the API (`uvicorn starrocks_br.api.app:app` or a new `starrocks-br api serve` command), then registers clusters via the API/CLI.
3. Rollback: stop the API process; no impact on existing direct CLI usage or on any target StarRocks cluster's `ops` schema, since the API's metadata store is entirely separate.
4. Future MySQL migration (not part of this change): point `DATABASE_URL` at MySQL/Postgres, run Alembic migrations against it, cut over.

## Open Questions

- Exact `SHOW BACKUP`/`SHOW RESTORE` column names/availability for progress on the target StarRocks version(s) in production - to be confirmed empirically during implementation (e.g. against the available test instance once a repository is configured there) without changing the spec (which already tolerates missing progress data).
