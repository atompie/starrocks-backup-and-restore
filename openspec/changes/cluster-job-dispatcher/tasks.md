## 1. Admission queries

- [ ] 1.1 Add DAL functions in `dal/metadata/jobs.py`: clusters with `PENDING` jobs, whether a cluster has a `RUNNING` job, the next `PENDING` job for a cluster ordered by priority then id, and `claim_pending_job(job_id, now)` as a conditional `PENDING` -> `RUNNING` update setting `started_at` and `heartbeat_at`; verify with unit tests (crud) including a lost claim.
- [ ] 1.2 Drop the `PENDING` branch from `list_stale_jobs`; verify a long-waiting `PENDING` job is never returned.

## 2. Dispatch step

- [ ] 2.1 Implement `commands/jobs.py::dispatch_pending_jobs(now)`: per cluster, skip when a job is `RUNNING`, otherwise admit the highest-priority `PENDING` job (restore, then backup, then other; oldest first), claim it, then enqueue on `job.backend`; mark the job `FAILED` if enqueue raises and continue; return a summary of admitted job ids; verify service tests for same-cluster serialization, cross-cluster parallelism, priority order and enqueue failure.
- [ ] 2.2 Change `submit_job` to create the `PENDING` job without enqueueing; update `commands/restore.py`, `commands/schedules.py` callers and any test that assumed immediate enqueue; verify a submitted job stays `PENDING` until dispatch.
- [ ] 2.3 Update `jobs/thread_backend._run_job` to stop calling `mark_running` and to return when the job is not `RUNNING`; verify unit tests.

## 3. Reconciliation and schedules

- [ ] 3.1 Remove the stale-`PENDING` re-enqueue from `reconcile_stale_jobs` and `ReconciliationSummary.requeued`; verify a job admitted then abandoned is failed as a stale `RUNNING` job on a later tick.
- [ ] 3.2 Make `run_due_schedules` skip submitting for a schedule that already has a `PENDING` or `RUNNING` job while still advancing `next_run_at`; verify a backup longer than its cadence does not stack jobs.

## 4. Tick wiring and CLI

- [ ] 4.1 Call `dispatch_pending_jobs` in `execute_scheduler_tick` after `run_due_schedules` and `expire_due_schedules`, record admitted ids in `TickResult`, and keep `_wait_for_workers`; verify a tick admits and runs a job created in the same tick.
- [ ] 4.2 Print dispatch counts in `cli/scheduler.py`; confirm `POST /backup/schedules/run` only queues jobs; verify with unit tests.

## 5. Verification and docs

- [ ] 5.1 Add a test where two overlapping tick invocations cannot admit two jobs for one cluster.
- [ ] 5.2 Remove tests that assume a job starts outside a tick, and fix or audit the rest:
  - Delete `tests/integration/test_full_backup_restore_cycle.py` and `tests/integration/test_schedule_cleanup_cycle.py`; replacement integration tests are written later (see `add-scheduled-backup-restore-integration-test`) and must drive jobs through a tick.
  - In `tests/unit/service/test_job_reconciliation.py` delete `test_fresh_pending_job_is_not_reenqueued` and `test_stale_pending_job_is_claimed_and_reenqueued_once`, and remove the `requeued` assertion from `test_live_running_job_is_left_completely_alone`.
  - In `test_jobs_thread_backend.py` and `test_thread_backend_heartbeat.py` create each job `RUNNING` before `backend.enqueue`, since the worker no longer performs that transition.
  - In `test_api_jobs.py` and `test_api_schedules.py` make every test that uses `_wait_for_terminal` run a scheduler tick (or `dispatch_pending_jobs`) after submission and before polling.
  - In `test_commands_jobs.py` drop or invert assertions that `submit_job` enqueues on the backend (`_FakeBackend.enqueued`); keep the `group_id` tests.
  - Add the `test_cli_scheduler.py` tick-step fixture's dispatch step.
  - Audit line by line against the new design and fix or delete as needed: `test_dal_jobs.py` and `test_jobs_heartbeat_dal.py` (any `list_stale_jobs` assertion that includes `PENDING`), `test_schedule_cleanup.py` (fake backend enqueue), `test_commands_schedules.py`, and the remaining tests in `test_api_jobs.py` and `test_api_schedules.py`.
  - Verify that no remaining test depends on a job starting without a tick, and that the suite passes.
- [ ] 5.3 Update README/operations notes: a tick is required for any job to start, and how to check `GET /health`.
