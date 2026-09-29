## Context

The scheduler tick (`commands/schedules.py::execute_scheduler_tick`, run by `starrocks-br-scheduler tick`) is a
one-shot process. It takes the singleton scheduler lock, reconciles stale jobs, submits due schedules and
expires one-shots, releases the lock, and waits for its in-process workers. Jobs run in a `ThreadBackend` pool,
concurrently. The tick itself never runs a job, so "one tick at a time" does not serialize jobs: two schedules
due in the same tick, or an API-submitted job, already run in parallel and are separated only by the
`RunStatus` slot check made when a job starts (`concurrency.reserve_job_slot`), where the loser fails.

This change makes the tick the single place that decides what runs next on each cluster.

## Goals / Non-Goals

**Goals:**
- One job at a time per cluster, for every job type; different clusters run in parallel.
- A job that has to wait is queued (`PENDING`), never failed.
- Deterministic ordering: restore, then backup, then other work; oldest first.
- Crash-safe: queued jobs live in the database; a job whose worker died is recovered by reconciliation.

**Non-Goals:**
- Parallelism inside one cluster, or preemption of a running job.
- Removing `RunStatus` slots or their stale healing (kept as a backstop).
- Starting the next job the instant the previous one ends (it starts on the next tick).
- Changing how jobs execute once admitted.

## Decisions

### 1. Submission queues, the tick admits

`commands.jobs.submit_job` creates the `Job` (`PENDING`, committed) and returns; it no longer calls
`backend.enqueue`. Enqueueing moves to a new `commands.jobs.dispatch_pending_jobs(now)` called by
`execute_scheduler_tick` after reconciliation, `run_due_schedules` and `expire_due_schedules`, so jobs created
earlier in the same tick can start in it. Callers of `submit_job` (restore submission, schedule creation for a
one-shot, run-due, schedule deletion, expiry) are unchanged.

*Alternative*: keep enqueue-on-submit and add waiting in `reserve_job_slot`. Rejected: it parks a pool thread per
waiting job, needs TTLs and preemption rules to stay safe, and leaves ordering to whoever polls first.

### 2. Admission rule

For each cluster that has `PENDING` jobs:
1. If any job on the cluster is `RUNNING`, skip the cluster this tick.
2. Otherwise take the highest-priority `PENDING` job: priority 0 = `restore`; 1 = `backup_full`,
   `backup_incremental`; 2 = everything else. Ties break on lowest `id`.
3. Claim it with a conditional `UPDATE ... SET status='RUNNING', started_at=now, heartbeat_at=now WHERE id=? AND
   status='PENDING'`. Only if the update matched a row, enqueue it on `job.backend`.

At most one job is admitted per cluster per tick. Clusters are independent, so a tick can admit one job on each of
several clusters. Admission touches metadata only and never contacts StarRocks. Total parallelism stays bounded
by the backend pool size.

The scheduler lock already serializes dispatchers; the conditional claim is defense in depth (and protects a
lock reclaimed after expiry).

*Rationale for marking `RUNNING` at admission*: if the worker did it, a job would be enqueued yet still `PENDING`,
and the next tick would admit a second job on that cluster.

If `enqueue` raises (backend unavailable), the job is marked `FAILED` with the error and the pass continues.

### 3. Worker no longer starts the job

`jobs/thread_backend._run_job` stops calling `jobs_dal.mark_running`; it loads the job, verifies it is `RUNNING`
(else returns), and proceeds with heartbeat and handler as today. `heartbeat_at` is set at admission, so a worker that
never starts still goes stale.

### 4. Reconciliation stops re-enqueueing `PENDING`

`reconcile_stale_jobs` only considers `RUNNING` jobs. A `PENDING` job's age is queue time, not staleness, so
`list_stale_jobs` drops the `PENDING` branch and `ReconciliationSummary.requeued` is removed. A job admitted and
lost before its worker ran is `RUNNING` with a stale heartbeat and is handled by the existing `RUNNING` path (a backup
with no matching StarRocks operation is failed). It is not retried automatically; a schedule's next occurrence
covers it. Reconciliation runs before dispatch, so a freed cluster can admit its next job in the same tick.

### 5. Recurring schedules coalesce

`run_due_schedules` still advances `next_run_at` for each due schedule (idempotency unchanged) but submits a new job
only if the schedule has no `PENDING` or `RUNNING` job. The skipped occurrence is logged. Without this, a backup
longer than its cadence stacks unbounded queued jobs.

### 6. `RunStatus` slots stay as a backstop

Backups still reserve and complete their `backup` slot inside the handler. With one job per cluster at a time the
slot should never conflict; if it does (for example a job started outside the dispatcher) the backup fails as today.
Removing or simplifying slots is a separate change.

### 7. What starts jobs

Only `execute_scheduler_tick`. `POST /backup/schedules/run` keeps submitting due schedules but only queues them; it
does not dispatch (it does not take the scheduler lock). The API process no longer runs jobs. `GET /health`'s scheduler
liveness is the operator's signal that nothing is starting.

## Risks / Trade-offs

- **[Risk]** No tick means nothing runs, including restores. *Mitigation*: documented; `/health` reports `last_tick_at`;
  restores are the highest priority once a tick runs.
- **[Trade-off]** Start latency is up to one tick interval, and consecutive jobs on a cluster start one tick apart.
  Acceptable for backup/restore; a future change can let a finishing worker trigger dispatch for its cluster.
- **[Risk]** Starvation of low-priority work (retention, cleanup) on a cluster that is never idle. *Mitigation*: accepted;
  it is cleanup and clusters are not saturated in practice.
- **[Risk]** A worker dying between admission and start. *Mitigation*: stale `RUNNING` reconciliation (Decision 4).

## Migration Plan

1. Add DAL queries and the conditional claim; add `dispatch_pending_jobs`.
2. Change `submit_job` to queue only; update `thread_backend._run_job`.
3. Drop `PENDING` re-enqueue from reconciliation.
4. Coalesce in `run_due_schedules`; wire dispatch into `execute_scheduler_tick`; surface counts in CLI output.
5. Update tests and docs. No test may depend on a job starting outside a tick: the two integration tests that do are deleted and rewritten later as tick-driven tests, and unit/service tests that poll for a job to finish run a tick first. Rollback: revert the commit; no data migration is involved. Jobs left `PENDING` by a rolled-back
   deployment are re-enqueued by the old reconciliation.
