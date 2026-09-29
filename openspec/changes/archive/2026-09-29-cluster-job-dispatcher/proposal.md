## Why

Today a job starts the moment it is submitted: `submit_job` creates the `Job` row and enqueues it on a
worker pool immediately, and the only thing that keeps two jobs from colliding on one cluster is a
`RunStatus` slot check made when the job starts. The loser of that race fails with
`ConcurrencyConflictError`. Two schedules due in the same tick, a one-shot schedule created through the
API while a backup runs, and (soon) automatic retention jobs all hit this. Ordering work on a cluster
should be a scheduling decision made in one place, not a race that fails jobs.

## What Changes

- **Submitting a job only queues it**: `submit_job` creates the `Job` in `PENDING` and no longer enqueues
  it on a backend. The HTTP 202 contract is unchanged.
- **Dispatch step in the scheduler tick**: after reconciliation, due-schedule submission and one-shot
  expiry, the tick admits queued jobs. Per cluster it admits at most one job, and only when no job is
  `RUNNING` on that cluster; different clusters are admitted independently and run in parallel.
- **Priority within a cluster lane**: restore first, then backup (full/incremental), then everything else
  (schedule cleanup, retention, ...), oldest first within a priority. One lane per cluster for all job types.
- **Admission marks the job `RUNNING`** (conditional `PENDING` -> `RUNNING` update setting `started_at` and
  `heartbeat_at`) before handing it to the backend, so overlapping tick processes cannot admit two jobs for
  one cluster. The worker no longer performs that transition.
- **Reconciliation no longer re-enqueues `PENDING` jobs** (**BREAKING** for the current CLI behavior):
  a queued job waiting its turn is normal, not stale. A job that was admitted but whose worker died goes stale
  as `RUNNING` and is failed by the existing reconciliation.
- **Recurring schedules do not stack jobs**: a due schedule that already has a `PENDING` or `RUNNING` job
  advances `next_run_at` without submitting another job.
- **`POST /backup/schedules/run`** keeps submitting due schedules but only queues them; jobs start on the next
  tick (**BREAKING** for clients that relied on immediate start).
- `RunStatus` slots and their stale healing stay in place as a backstop and are unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-job-execution`: submitted jobs stay `PENDING` until a scheduler tick admits them; adds the
  one-job-per-cluster lane, priority order and cross-cluster parallelism.
- `api-scheduling`: run-due submits queued jobs and does not submit a second job for a schedule that still
  has an unfinished one.
- `cli`: the tick gains the dispatch step; reconciliation stops re-enqueueing stale `PENDING` jobs.

## Impact

- **Behavior**: no job of any type starts unless a tick runs. `GET /health` already reports scheduler
  liveness (`last_tick_at`). Start latency for an API-submitted restore or one-shot backup is up to one tick
  interval, and the next job on a busy cluster starts on the tick after the previous one ends.
- **Code**: `commands/jobs.py` (submit, new dispatch step, reconciliation), `dal/metadata/jobs.py`
  (admission queries and conditional claim), `commands/schedules.py` (`run_due_schedules`,
  `execute_scheduler_tick`), `jobs/thread_backend.py` (`_run_job` no longer calls `mark_running`),
  `cli/scheduler.py` output.
- **No schema migration**: uses the existing `jobs` columns and statuses.
- **Follow-up**: `schedule-scoped-retention` depends on this change and becomes much simpler (no slot
  handling, no post-success hook, no preemption).
