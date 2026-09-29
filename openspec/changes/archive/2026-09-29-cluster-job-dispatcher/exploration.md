# Exploration: Cluster Job Dispatcher (one lane per cluster, ordered by the tick)

This document preserves the exploration that led to `cluster-job-dispatcher`. It started as a question about
how automatic retention should coexist with backups (`schedule-scoped-retention`) and ended with a broader
decision: the scheduler tick should decide what runs next on each cluster, instead of jobs racing for a slot.

---

## 1. How the system behaves today

```text
 tick process (cron, one-shot, holds scheduler lock)
   reconcile stale jobs -> run_due_schedules -> expire_due_schedules
        |                        |
        |                 submit_job() = create Job row + backend.enqueue()   (returns at once)
        v
   lock released, process waits for its own workers (_wait_for_workers)

 ThreadBackend pool (8 workers) - jobs run HERE, concurrently
   worker 1: backup A ........
   worker 2: backup B ........   <- two schedules due in the same tick
   API process pool: restore C ..   <- API-submitted jobs run in the API process, start at once
```

Facts verified in code:
- The tick is sequential (singleton `scheduler_lock`), but it only *creates and enqueues* jobs. The jobs run in
  parallel in a worker pool, so "one tick at a time" does not serialize them.
- The only guard against two jobs colliding on one cluster is `concurrency.reserve_job_slot` (a `RunStatus` row,
  scope `backup`) checked when a job *starts*. On conflict it raises `ConcurrencyConflictError`; a backup that
  hits it becomes `FAILED`.
- Stale-slot healing (`is_backup_job_stale`) asks StarRocks `SHOW BACKUP` about a *label* and returns "stale" when
  no database lists that label. That is a poor fit for anything that is not a StarRocks backup.
- Reconciliation (Section 9) re-enqueues stale `PENDING` jobs by `created_at`. Under a queue, waiting is normal.
- `POST /backup/schedules/run` only calls `run_due_schedules` (no lock, no dispatch).

## 2. Options considered

```text
+-----+---------------------------------------------+------------------------------------------------+
| Opt | Idea                                        | Verdict                                        |
+-----+---------------------------------------------+------------------------------------------------+
| A   | Keep race-at-start, make retention          | Needs labels, preemption, heartbeat heal;      |
|     | preemptible (backup cancels its slot)       | accidental "stale" behavior made explicit.     |
| B   | Backup waits (polls) for retention, slot    | Parks a pool thread per waiter; needs TTL and  |
|     | has expires_at                              | expiry rules; ordering still first-come.       |
| C   | Separate retention tick / loop              | Still needs the slot (check-then-act); not     |
|     |                                             | sequenced with the backup tick.                |
| D   | Run retention inline at end of the backup   | No new concurrency, but conflicts with         |
|     | job under its slot                          | SPEC 23 (retention is its own Job) and         |
|     |                                             | failure isolation.                             |
| E   | Tick decides what runs next per cluster     | CHOSEN. One lane per cluster, queued jobs are  |
|     | (dispatcher); jobs wait as PENDING          | PENDING rows; no failing, no polling.          |
+-----+---------------------------------------------+------------------------------------------------+
```

Reasoning that led to E: the user's intent is "different clusters can run concurrently, jobs on the same cluster
one after another, and one place makes sure of it for backups, retention and other processes". That is admission
control, not a lock race.

## 3. The chosen model

```text
 API / run-due / retention sweep / schedule deletion
        |  submit_job(): create Job (PENDING) ... nobody enqueues
        v
 +------------------------------ tick (scheduler lock) -------------------------------+
 | 1 reconcile stale RUNNING jobs   (frees clusters whose job died)                     |
 | 2 run_due_schedules              (skips a schedule that still has an open job)       |
 | 3 expire_due_schedules                                                               |
 | 4 dispatch_pending_jobs                                                              |
 |     for each cluster with PENDING jobs:                                              |
 |        any job RUNNING on it?  yes -> skip cluster                                   |
 |        no -> pick highest priority (restore > backup > other), oldest first          |
 |              conditional UPDATE PENDING->RUNNING (started_at, heartbeat_at)          |
 |              matched -> backend.enqueue(job)                                         |
 +--------------------------------------------------------------------------------------+
 cluster 1: [ backup ......... ]   [ retention .. ]   <- one at a time, next one next tick
 cluster 2: [ restore .. ] [ backup ........ ]         <- independent lane, in parallel
```

Confirmed decisions (from the discussion):
- One lane per cluster for *all* job types; different clusters in parallel.
- Priority: restore, then backup, then everything else (retention, schedule cleanup); oldest first.
- Everything goes through a tick: no tick, nothing starts (including restores). `GET /health` `last_tick_at` is
  the operator's signal.
- A job that has to wait is `PENDING`, never failed.
- `RunStatus` slots remain as a backstop; removing them is a separate change.

## 4. Consequences worth remembering

- **Admission must set `RUNNING`**: if the worker did it, the next tick would see no `RUNNING` job and admit a
  second one. So the claim moves from `_run_job` to the dispatcher.
- **Reconciliation changes**: stale `PENDING` re-enqueue is removed (age is queue time). A job admitted and lost
  before its worker ran is `RUNNING` with a stale heartbeat and is failed by the existing path.
- **Latency**: up to one tick interval to start; the next job on a cluster starts on the tick after the previous
  one ends. A finishing worker triggering dispatch for its cluster is a possible later optimization.
- **Stacked schedules**: a backup slower than its cadence would queue unbounded jobs, so `run_due_schedules`
  skips submitting for a schedule with a `PENDING`/`RUNNING` job (still advancing `next_run_at`).
- **Execution location**: the process that runs the tick runs the admitted jobs. The API process no longer runs
  jobs, and `POST /backup/schedules/run` only queues.
- **Starvation** of retention/cleanup on a permanently busy cluster is accepted.

## 5. Interaction with `schedule-scoped-retention`

`schedule-scoped-retention` depends on this change. It no longer needs a post-success hook, slot handling,
preemption or expiry TTLs: a retention job is a `PENDING` job created by a tick sweep, and the lane guarantees it
never overlaps a backup, restore or cleanup. Archive `cluster-job-dispatcher` first; the retention change's delta
for "Submitting an operation returns immediately with a job" is written against the post-dispatcher wording.

## 6. Open questions / non-goals

- Should a finishing worker trigger dispatch for its cluster to cut inter-job latency? (deferred)
- Should `POST /backup/schedules/run` also dispatch? (default: no, it does not hold the scheduler lock)
- Removing `RunStatus` slots and the `SHOW BACKUP` staleness heuristic (a job-heartbeat-only model) is out of scope.
