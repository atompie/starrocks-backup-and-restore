## Why

Currently, triggering due recurring backup schedules requires calling the HTTP endpoint `POST /backup/schedules/run`. In containerized and cloud environments (such as Kubernetes CronJobs or systemd timers), operators need a lightweight, process-local CLI command to execute a single scheduler tick without running an in-process daemon loop inside FastAPI or relying on network calls to the API. Because cron-style triggers can overlap and processes can be terminated unexpectedly, this command needs singleton concurrency locking, restart recovery for orphaned jobs, and liveness observability.

## What Changes

- Add a new CLI command `starrocks-br-scheduler tick` (also runnable via `python -m starrocks_br.cli.scheduler tick`) that executes a single scheduler tick per invocation and exits cleanly.
- Add a singleton `scheduler_lock` table in the metadata store with atomic conditional acquisition (`UPDATE ... WHERE expires_at IS NULL OR expires_at < :now`) and lease expiration (`STARROCKS_BR_SCHEDULER_LOCK_TIMEOUT_SECONDS`).
- Handle lock collision: if the lock is actively held, log to stderr and exit immediately with `EX_TEMPFAIL` (`75`) without evaluating schedules, running expiry, or modifying jobs.
- Self-heal stale locks: if a previous holder crashed and its lease expired, reclaim the lock with a warning log and proceed.
- Reconcile orphaned jobs before schedule evaluation: re-enqueue `PENDING` jobs; check StarRocks `SHOW BACKUP` / `SHOW RESTORE` for `RUNNING` jobs (`SUCCESS` if `FINISHED`, `FAILED` if `CANCELLED`/`LOST`, skip with a warning if the cluster is unreachable, leave `RUNNING` if still active).
- Run due recurring schedules and one-shot schedule expiry, advancing `next_run_at` to the next future occurrence after now (catch-up policy).
- Record `last_tick_at` on successful tick completion and surface it via `GET /health` (`scheduler.last_tick_at`).
- Release the scheduler lock in `finally`, then wait for any worker threads dispatched during that tick (`backend.shutdown(wait=True)`) before exiting.
- Update `api-scheduling` documentation to specify both HTTP (`POST /backup/schedules/run`) and CLI tick triggers as valid execution mechanisms.

## Capabilities

### New Capabilities
- `cli`: Command-line interface providing a single-tick scheduler command (`starrocks-br-scheduler tick`) with metadata-backed concurrency locking, orphaned-job reconciliation, due schedule dispatch, one-shot expiry execution, thread wait, and health recording.

### Modified Capabilities
- `api-scheduling`: Document both supported execution trigger mechanisms — HTTP endpoint `POST /backup/schedules/run` and process-local CLI tick command.

## Impact

- CLI entry points added in `pyproject.toml` (`[project.scripts]`) and `src/starrocks_br/cli/scheduler.py`.
- Metadata store schema migration: new table `scheduler_lock` storing singleton lock state and `last_tick_at`.
- DAL modules: metadata lock management in `src/starrocks_br/dal/metadata/scheduler_lock.py` (or `schedules.py`).
- Commands layer: `commands/schedules.py` gains lock acquisition/release and tick recording; `commands/jobs.py` gains orphaned job reconciliation.
- API route: `GET /health` updated to surface `scheduler.last_tick_at`.
- No backward-incompatible changes to existing HTTP API routes or schedule schemas.
