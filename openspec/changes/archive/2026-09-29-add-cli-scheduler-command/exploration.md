# Section 9 Exploration: Scheduler CLI Command, Concurrency, and Restart Recovery

This document preserves the comprehensive exploration, architectural analysis, diagrams, and decisions established for Section 9 of `PLAN.md` (`add-cli-scheduler-command`).

---

## 1. High-Level Architectural Context

Originally, Section 9 envisioned an in-process asyncio/threading loop inside FastAPI running every `STARROCKS_BR_SCHEDULER_INTERVAL_SECONDS`. That was deliberately superseded in favour of an **externally-invoked CLI tick command** (triggered by OS cron, systemd timer, or Kubernetes `CronJob`).

```text
+-------------------+       +--------------------+
| OS Cron / Systemd |       |  HTTP API Client   |
|   Timer / K8s     |       | (cron / operator)  |
+---------+---------+       +---------+----------+
          | (periodic tick)           | POST /backup/schedules/run
          v                           v
+-------------------+       +--------------------+
| starrocks-br cli  |       | FastAPI API Route  |
|  scheduler tick   |       | (api/routes/...py) |
+---------+---------+       +---------+----------+
          |                           |
          +-------------+-------------+
                        |
                        v
        +-------------------------------+
        |        commands layer         |
        | (commands/schedules.py, etc.) |
        +---------------+---------------+
                        |
         +--------------+--------------+
         v                             v
+-----------------+           +-----------------+
|  StarRocks DB   |           | Metadata Store  |
| (SHOW BACKUP/   |           | (scheduler_lock,|
|  RESTORE, FE)   |           |  schedules,     |
+-----------------+           |  jobs, history) |
                              +-----------------+
```

### Architectural Guardrails
1. **Dual Supported Triggers (Decision Q13):** Both `POST /backup/schedules/run` (HTTP) and `starrocks-br-scheduler tick` (CLI) are fully supported ways to trigger due-schedule execution. The CLI is additive, not a replacement for the API endpoint.
2. **Layering Boundary (`AGENTS.md`):** The CLI is strictly a presentation/entry-point layer. Like FastAPI routes, it **must only call the commands layer** (`commands/schedules.py`, `commands/jobs.py`), never calling core operation modules (`executor`, `planner`, `restore`) or the DAL directly.
3. **No Daemon State in the CLI:** The CLI executes a single tick and exits; cadence, enable/disable switches, and retry timing belong to the external orchestrator.
4. **Short-Lived Metadata Sessions:** Database transactions must be closed before network calls to StarRocks (e.g. `SHOW BACKUP`), and reopened only to record state changes.

---

## 2. The Four Pillars of Section 9

```text
CLI Invocation
      |
      v
+-----------------------------------------------------------------------------+
| Pillar 1: Concurrency & Lock                                                |
| - Try acquire singleton `scheduler_lock` row (atomic conditional UPDATE)    |
| - If held & active: log to stderr & exit with distinct non-zero code        |
| - If stale (past `expires_at`): reclaim & log warning                       |
+-----------------------------------------------------------------------------+
      | (Lock acquired)
      v
+-----------------------------------------------------------------------------+
| Pillar 2: Restart Recovery & Job Reconciliation                             |
| - `PENDING` jobs: re-enqueue to backend                                     |
| - `RUNNING` jobs: query StarRocks `SHOW BACKUP` / `SHOW RESTORE`            |
|   -> If FINISHED: mark SUCCESS, save references, append event               |
|   -> If CANCELLED/LOST: mark FAILED, append event                           |
|   -> If still ACTIVE: leave RUNNING for future tick                         |
|   -> If cluster unreachable: log warning and skip                           |
+-----------------------------------------------------------------------------+
      |
      v
+-----------------------------------------------------------------------------+
| Pillar 3: Due Schedules & One-Shot Expiry Execution                         |
| - Run `commands.schedules.run_due_schedules` (advances next_run_at)          |
| - Run one-shot schedule expiry cleanup (delegates to schedule_cleanup)      |
+-----------------------------------------------------------------------------+
      |
      v
+-----------------------------------------------------------------------------+
| Pillar 4: Observability & Release (finally block)                           |
| - Update `last_tick_at` in metadata store (surfaced via GET /health)        |
| - Release `scheduler_lock`                                                  |
| - Await worker threads (`backend.shutdown(wait=True)`) before process exit  |
+-----------------------------------------------------------------------------+
```

---

## 3. Deep Dive on the Four Exploration Threads

### Thread 3.1: ThreadBackend Lifecycle & Lock Release (Option A vs. Option B)

When `ThreadBackend` (in-process `ThreadPoolExecutor`) is used, should the process wait for workers, and when should the lock be released?

#### Option A: Release lock immediately after dispatch, wait for threads to finish (Selected)

```text
Time   CLI Tick Process (Main Thread)               Worker Thread(s)             Scheduler Lock State
----   ------------------------------               ----------------             --------------------
T0     Acquire `scheduler_lock`                                                  HELD (holder=host:pid)
T1     Reconcile stale jobs (heartbeat-expired only)
T2     `run_due_schedules` (advances next_run_at)  --> Enqueue job(s)
T3     `expire_due_schedules`
T4     Record `last_tick_at`
T5     RELEASE `scheduler_lock` (in finally)                                     FREE
       |
T6     Wait for worker threads (ThreadBackend)      ==> Executing backup ...
       |                                                (held by cluster slot)
T7     Worker threads complete                      ==> Backup FINISHED
T8     Process exits (0)
```

**Why Option A was chosen:**
1. **Lock timeout safety:** The `scheduler_lock` is only held for the few hundred milliseconds needed to evaluate schedules and submit jobs. It never risks lease expiration during a multi-hour backup, allowing `STARROCKS_BR_SCHEDULER_LOCK_TIMEOUT_SECONDS` (default 300s) to remain a tight guard against crashed processes.
2. **Multi-cluster parallelism:** If Cluster A has a 45-minute backup running in Tick 1, Tick 2 (firing 5 minutes later) can acquire `scheduler_lock` and trigger a due backup for Cluster B.
3. **Cluster A remains protected:** Cluster A is already locked by `concurrency.reserve_job_slot(scope="backup")`, and its `next_run_at` was already advanced, so Tick 2 will not re-trigger Cluster A.
4. **Clean container shutdown:** Calling `backend.shutdown(wait=True)` before `sys.exit(0)` prevents Kubernetes from prematurely terminating a container while worker threads are still uploading data.

#### Option B: Hold `scheduler_lock` for the entire job execution (Rejected)

```text
Time   CLI Tick Process                             Worker Thread(s)             Scheduler Lock State
----   ----------------                             ----------------             --------------------
T0     Acquire `scheduler_lock`                                                  HELD
T1     Reconcile, trigger jobs                     --> Enqueue job(s)
T2     Wait for worker threads                     ==> Executing backup ...      HELD (for 45 mins!)
       ...                                                                       (Lock expires after 5m!)
T3     Release `scheduler_lock`                                                  FREE
```

*Rejected because:* Multi-hour backups would cause the lock lease to expire, leading subsequent ticks to treat the active lock as stale and steal it. It also halts backup scheduling across all other healthy clusters.

---

### Thread 3.2: Sequencing Dependency with Section 8

- **Confirmed Decision:** Section 8 (`retire-manual-backup-routes-and-schedule-cleanup`) lands first.
- **Impact:** Section 8 introduces the `schedule_cleanup` job type and cascading delete logic. Section 9's tick implementation can directly invoke `commands.schedules.expire_due_schedules(db, now)` without needing stubs or temporary mocks.

---

### Thread 3.3: CLI Exit Codes & Contention Handling

- **Confirmed Decision:** Exit code `os.EX_TEMPFAIL` (`75` from `<sysexits.h>`).
- **Exit Code Convention:**
  - `0`: Tick succeeded.
  - `75` (`EX_TEMPFAIL`): Temporary lock contention (another tick is currently running).
  - `1`: Unexpected execution error / unhandled crash.
  - `2`: CLI usage / argument syntax error.
- **Monitoring Value:** Schedulers (cron wrappers, Kubernetes alerting) can differentiate between a fatal crash (exit 1) and expected concurrency contention (exit 75), preventing false-positive alert spam.

---

### Thread 3.4: Resilient Multi-Cluster Reconciliation

When reconciling jobs left in `RUNNING` status across multiple registered clusters:

```text
Reconciliation Loop
        |
        v
For each RUNNING job in metadata DB:
        |
        +---> Resolve target Cluster
        |
        +---> Attempt connection to Cluster FE
                 |
                 +---[Connection Failed / Timeout]---> Log WARNING & continue:
                 |                                     "Could not reach cluster %s (job %s): %s; skipping"
                 |                                     (Job remains RUNNING for next tick)
                 |
                 +---[Connected]---> Query SHOW BACKUP / SHOW RESTORE
                                       |
                                       +--- FINISHED:  Mark SUCCESS, record refs, complete slot
                                       +--- FAILED:    Mark FAILED, log error, complete slot
                                       +--- ACTIVE:    Leave RUNNING (still in progress in StarRocks)
```

- **Confirmed Decision:** If a cluster FE is unreachable, the error is logged as a warning, the job is left in `RUNNING`, and reconciliation continues for remaining jobs. One offline cluster will never block backup scheduling for all other healthy clusters.

---

## 4. Concurrency Mechanics: Singleton `scheduler_lock`

A dedicated `scheduler_lock` table holds a single row (`id = 1`):

```sql
CREATE TABLE scheduler_lock (
    id INTEGER PRIMARY KEY,
    holder VARCHAR(255),
    acquired_at TIMESTAMP,
    expires_at TIMESTAMP,
    last_tick_at TIMESTAMP
);
```

### Atomic Conditional Acquisition
```sql
UPDATE scheduler_lock
SET holder = :holder,
    acquired_at = :now,
    expires_at = :expires_at
WHERE id = 1
  AND (expires_at IS NULL OR expires_at < :now);
```
- If `rowcount == 1`: Lock acquired.
- If `rowcount == 0`: Lock is actively held by another process. Exit with code 75.
- Stale check: If previous `expires_at < :now`, log:
  `logger.warning("Recovered stale scheduler lock previously held by %s", prev_holder)`

### Safe Lock Release
```sql
UPDATE scheduler_lock
SET holder = NULL,
    acquired_at = NULL,
    expires_at = NULL
WHERE id = 1 AND holder = :holder;
```
Ensures that a process whose lock timed out and was stolen cannot clear the lock of the new holder.

---

## 5. End-to-End Execution Flow

```text
+-----------------------------------------------------------------------------------+
|                        starrocks-br-scheduler tick                                |
+-----------------------------------------------------------------------------------+
  |
  +-- 1. commands.schedules.try_acquire_scheduler_lock()
  |      [DB: UPDATE scheduler_lock WHERE expires_at IS NULL OR expires_at < :now]
  |      |-- rowcount == 0    --> stderr: "scheduler already running" -> exit 75
  |      \-- is_stale == True --> logger.warning("Recovered stale lock from %s", prev_holder)
  |
  +-- 2. try:
  |      |-- commands.jobs.reconcile_stale_jobs()
  |      |   |-- PENDING: re-enqueue to backend
  |      |   \-- RUNNING: check SHOW BACKUP/RESTORE (skip unreachable clusters with warning)
  |      |
  |      |-- commands.schedules.run_due_schedules()
  |      |   \-- advances next_run_at, submits jobs to backend
  |      |
  |      |-- commands.schedules.expire_due_schedules() [Section 8]
  |      |   \-- submits schedule_cleanup jobs for expired one-shots
  |      |
  |      \-- commands.schedules.record_last_tick_at()
  |          \-- updates scheduler_lock.last_tick_at (observable via GET /health)
  |
  +-- 3. finally:
  |      \-- commands.schedules.release_scheduler_lock()
  |          \-- clears holder, acquired_at, expires_at where holder = self
  |
  +-- 4. backend.shutdown(wait=True)
  |      \-- Wait for ThreadBackend worker threads to complete
  |
  \-- 5. exit 0
```
