## 1. Schema & Data Access Layer

- [x] 1.1 Add `SchedulerLock` model in `src/starrocks_br/store/models.py` with singleton `id=1`, `holder`, `acquired_at`, `expires_at`, and `last_tick_at`. Verify model compiles and imports cleanly.
- [x] 1.2 Create Alembic migration under `src/starrocks_br/store/migrations/versions/` creating `scheduler_lock` table and seeding initial singleton row `id=1`. Verify migration applies cleanly with `alembic upgrade head`.
- [x] 1.3 Add nullable `Job.heartbeat_at` in `src/starrocks_br/store/models.py` and an Alembic migration; add settings `STARROCKS_BR_JOB_HEARTBEAT_SECONDS` (30) and `STARROCKS_BR_JOB_STALE_SECONDS` (180), rejecting a stale value below 3x the heartbeat. Verify migration applies and config validation is unit tested.
- [x] 1.4 Add DAL functions in `dal/metadata/jobs.py`: `touch_heartbeat`, `list_stale_jobs`, and `claim_stale_job` (atomic conditional `UPDATE ... WHERE status = :old AND still stale`, returning rowcount). Verify in `tests/unit/crud/test_jobs_heartbeat_dal.py`, including the concurrent-claim case.
- [x] 1.5 Implement metadata DAL functions in `src/starrocks_br/dal/metadata/scheduler_lock.py` (`try_acquire`, `release`, `record_last_tick`, `get_last_tick_at`) using atomic conditional update for acquisition. Verify with unit tests in `tests/unit/crud/test_scheduler_lock_dal.py`.

- [x] 1.6 Add the heartbeat thread to `ThreadBackend._run_job` (own short session per write, stopped in `finally`); `mark_running` sets `heartbeat_at`. Verify in `tests/unit/service/test_thread_backend_heartbeat.py`: advances without progress callbacks, stops on success and on failure.

## 2. Commands Layer: Locking, Reconciliation & Tick Orchestration

- [x] 2.1 Add `try_acquire_scheduler_lock` and `release_scheduler_lock` in `src/starrocks_br/commands/schedules.py`, configuring lease duration via `STARROCKS_BR_SCHEDULER_LOCK_TIMEOUT_SECONDS` (default 300). Verify unit tests cover free acquisition, active contention rejection, and stale reclamation.
- [x] 2.2 Add `reconcile_stale_jobs` in `src/starrocks_br/commands/jobs.py` implementing the design's Decision 4 (stale `PENDING` re-enqueue; stale `RUNNING` backup/restore left `RUNNING` only while `SHOW BACKUP`/`SHOW RESTORE` shows the operation in progress, otherwise `FAILED` — never promoted to `SUCCESS`; no-label and non-backup stale jobs failed; unreachable clusters skipped with a warning; live jobs untouched; each job claimed atomically; slot released on terminal transitions). Verify with unit tests in `tests/unit/service/test_job_reconciliation.py`, including live-`RUNNING` and fresh-`PENDING` jobs left alone.
- [x] 2.3 Implement `execute_scheduler_tick` in `src/starrocks_br/commands/schedules.py` coordinating lock acquisition, stale job reconciliation, `run_due_schedules`, `expire_due_schedules`, recording `last_tick_at`, and lock release in `finally`. Verify unit tests in `tests/unit/service/test_commands_schedules.py`.

## 3. Observability in Health Endpoint

- [x] 3.1 Update `GET /health` in `src/starrocks_br/api/routes/health.py` to read `scheduler_lock.last_tick_at` and return `{"status": "ok", "scheduler": {"last_tick_at": ...}}`. Verify tests in `tests/unit/service/test_api_health.py` cover null and populated timestamps.

## 4. CLI Command & Process Lifecycle

- [x] 4.1 Implement `src/starrocks_br/cli/scheduler.py` with `argparse` handling `tick`, formatting holder identity (`hostname:pid`), exiting with `EX_TEMPFAIL` (`75`) on contention, and invoking `backend.shutdown(wait=True)` after lock release before exiting with code 0. Verify with CLI invocation tests.
- [x] 4.2 Register `starrocks-br-scheduler = "starrocks_br.cli.scheduler:main"` in `pyproject.toml` under `[project.scripts]`. Verify console script and `python -m starrocks_br.cli.scheduler tick` can be invoked.
- [x] 4.3 Add unit tests in `tests/unit/service/test_cli_scheduler.py` verifying standard exit, contention exit code 75, stale lock warning, and clean thread completion.

## 5. Verification

- [x] 5.1 Run full unit test suite `pytest tests/unit` and verify all tests pass.
- [x] 5.2 Validate OpenSpec change definition using `openspec validate add-cli-scheduler-command --strict`.
