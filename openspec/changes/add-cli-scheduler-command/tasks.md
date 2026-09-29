## 1. Schema & Data Access Layer

- [ ] 1.1 Add `SchedulerLock` model in `src/starrocks_br/store/models.py` with singleton `id=1`, `holder`, `acquired_at`, `expires_at`, and `last_tick_at`. Verify model compiles and imports cleanly.
- [ ] 1.2 Create Alembic migration under `src/starrocks_br/store/migrations/versions/` creating `scheduler_lock` table and seeding initial singleton row `id=1`. Verify migration applies cleanly with `alembic upgrade head`.
- [ ] 1.3 Implement metadata DAL functions in `src/starrocks_br/dal/metadata/scheduler_lock.py` (`try_acquire`, `release`, `record_last_tick`, `get_last_tick_at`) using atomic conditional update for acquisition. Verify with unit tests in `tests/unit/crud/test_scheduler_lock_dal.py`.

## 2. Commands Layer: Locking, Reconciliation & Tick Orchestration

- [ ] 2.1 Add `try_acquire_scheduler_lock` and `release_scheduler_lock` in `src/starrocks_br/commands/schedules.py`, configuring lease duration via `STARROCKS_BR_SCHEDULER_LOCK_TIMEOUT_SECONDS` (default 300). Verify unit tests cover free acquisition, active contention rejection, and stale reclamation.
- [ ] 2.2 Add `reconcile_orphaned_jobs` in `src/starrocks_br/commands/jobs.py` that re-enqueues `PENDING` jobs and inspects StarRocks `SHOW BACKUP` / `SHOW RESTORE` for `RUNNING` jobs (completing `SUCCESS` with references or `FAILED`, keeping active jobs `RUNNING`, and logging a warning to skip unreachable clusters). Verify with unit tests in `tests/unit/service/test_job_reconciliation.py`.
- [ ] 2.3 Implement `execute_scheduler_tick` in `src/starrocks_br/commands/schedules.py` coordinating lock acquisition, orphaned job reconciliation, `run_due_schedules`, `expire_due_schedules`, recording `last_tick_at`, and lock release in `finally`. Verify unit tests in `tests/unit/service/test_commands_schedules.py`.

## 3. Observability in Health Endpoint

- [ ] 3.1 Update `GET /health` in `src/starrocks_br/api/routes/health.py` to read `scheduler_lock.last_tick_at` and return `{"status": "ok", "scheduler": {"last_tick_at": ...}}`. Verify tests in `tests/unit/service/test_api_health.py` cover null and populated timestamps.

## 4. CLI Command & Process Lifecycle

- [ ] 4.1 Implement `src/starrocks_br/cli/scheduler.py` with `argparse` handling `tick`, formatting holder identity (`hostname:pid`), exiting with `EX_TEMPFAIL` (`75`) on contention, and invoking `backend.shutdown(wait=True)` after lock release before exiting with code 0. Verify with CLI invocation tests.
- [ ] 4.2 Register `starrocks-br-scheduler = "starrocks_br.cli.scheduler:main"` in `pyproject.toml` under `[project.scripts]`. Verify console script and `python -m starrocks_br.cli.scheduler tick` can be invoked.
- [ ] 4.3 Add unit tests in `tests/unit/service/test_cli_scheduler.py` verifying standard exit, contention exit code 75, stale lock warning, and clean thread completion.

## 5. Verification

- [ ] 5.1 Run full unit test suite `pytest tests/unit` and verify all tests pass.
- [ ] 5.2 Validate OpenSpec change definition using `openspec validate --change add-cli-scheduler-command`.
