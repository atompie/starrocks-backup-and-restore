## Context

See `proposal.md` for motivation. Currently, recurring schedules are triggered only through `POST /backup/schedules/run`. Direct job execution uses `ThreadBackend` by default, and cluster-level operations are serialized per cluster by `concurrency.reserve_job_slot(scope="backup")`.

Under `AGENTS.md`, the architectural boundary requires that entry points (FastAPI routes and the new CLI) call only into `commands/`, never directly into core operation modules (`executor`, `planner`, `restore`) or the DAL. Furthermore, metadata transactions must remain short-lived and must not be held open across network calls to StarRocks.

## Goals / Non-Goals

**Goals:**
- Provide a single-tick CLI command (`starrocks-br-scheduler tick` and `python -m starrocks_br.cli.scheduler tick`) that evaluates due schedules and exits.
- Provide cross-database portable mutual exclusion via a singleton `scheduler_lock` metadata row.
- Exit immediately with `EX_TEMPFAIL` (`75`) on lock contention without modifying state.
- Automatically reclaim stale locks if a previous process crashed.
- Reconcile stale (heartbeat-expired) `PENDING` and `RUNNING` jobs on tick startup, without touching live jobs.
- Release `scheduler_lock` immediately after dispatch, then wait for worker threads (`backend.shutdown(wait=True)`) before process termination.
- Record `last_tick_at` on successful tick completion and surface it via `GET /health`.

**Non-Goals:**
- Resident daemon loop or in-process sleep/interval timers inside the CLI or FastAPI process.
- Reviving the removed YAML-config or Click CLI framework (Click, old config files).
- Replacing or modifying the cluster-scoped `concurrency.reserve_job_slot` mechanism.

## Decisions

### Decision 1: Single-tick invocation with Option A thread waiting
The CLI command runs exactly one tick per invocation:
1. Acquire `scheduler_lock`.
2. Reconcile stale jobs, run due schedules, run one-shot expiry, record `last_tick_at`.
3. In `finally`, release `scheduler_lock`.
4. Call `backend.shutdown(wait=True)` so in-process worker threads finish executing backups before the process exits.

*Rationale & Alternatives Considered:*
- *Alternative B (Hold lock until worker threads finish):* Rejected. A multi-hour backup would hold `scheduler_lock`, blocking ticks for all other clusters and risking lock lease timeout.
- *Alternative C (Exit immediately without waiting for threads):* Rejected. Container environments (Kubernetes `CronJob`) terminate the container when the main process exits, killing worker threads mid-backup. Option A ensures clean completion while keeping the lock lease short.

### Decision 2: Atomic conditional UPDATE for singleton `scheduler_lock`
A dedicated `scheduler_lock` table holds a single row (`id = 1`) with columns `holder`, `acquired_at`, `expires_at`, and `last_tick_at`.
Lock acquisition executes:
```sql
UPDATE scheduler_lock
SET holder = :holder,
    acquired_at = :now,
    expires_at = :expires_at
WHERE id = 1
  AND (expires_at IS NULL OR expires_at < :now);
```
*Rationale & Alternatives Considered:*
- *Database-specific advisory locks (e.g. `pg_advisory_lock`):* Rejected. Not portable to SQLite (the default) or MySQL.
- *Redis/etcd distributed lock:* Rejected. Adds unnecessary external operational dependencies.

### Decision 3: Contention exit code `EX_TEMPFAIL` (75)
When `rowcount == 0` during lock acquisition (meaning an active process holds the unexpired lock), the command writes `"scheduler already running\n"` to `stderr` and exits with status 75 (`os.EX_TEMPFAIL`).

*Rationale & Alternatives Considered:*
- *Exit 0:* Rejected. Misleads monitoring tools into reporting a successful tick when no evaluation occurred.
- *Exit 1:* Rejected. Collapses temporary contention with unexpected fatal crashes, causing alert fatigue.

### Decision 4: Resilient multi-cluster reconciliation of stale jobs only
Reconciliation acts only on jobs whose owner is no longer alive, as judged by the heartbeat in Decision 6. On tick start:
- `PENDING` jobs older than the stale threshold (by `created_at`): claim, then re-enqueue to `registry.get(job.backend).enqueue(job.id)`. Fresh `PENDING` jobs are left alone (an API process may be about to start them).
- Stale `RUNNING` backup/restore jobs: connect to the job's cluster and check StarRocks `SHOW BACKUP` / `SHOW RESTORE` for the job's operation (backup: the job's `label` in the databases of its inventory group; restore: the labels of its restore pair in the databases of its group/table). The only outcome that keeps the job alive is "still in progress":
  - Still active in StarRocks: Leave in `RUNNING` (the claim already refreshed its liveness, so it is re-checked after another stale window).
  - `FINISHED`, `CANCELLED`, `LOST`/not found, or no `label` recorded: Mark `FAILED`, append a `FAILED` history event, and for a backup release its concurrency slot. A `FINISHED` backup is **not** promoted to `SUCCESS`: the partition manifest behind its references lived only in the dead process's memory, and a multi-database job's later labels were never persisted, so a reconstructed manifest could be wrong. The failure message names the unregistered snapshot so an operator can drop it. A `FINISHED` restore step is likewise not trusted, because a restore may be a multi-step flow (base + incremental) whose remaining steps never ran.
  - Cluster connection error / timeout: Log a warning, leave in `RUNNING`, and continue reconciling remaining jobs.
- Stale `RUNNING` jobs of other types (`schedule_cleanup`, later `retention`): Mark `FAILED` with the reason in `error_message` (they have no history table); their handlers are retry-safe and can be resubmitted.
- `RUNNING` jobs with a fresh heartbeat are never touched, whichever process owns them.

*Rationale & Alternatives Considered:*
- *Failing tick on cluster error:* Rejected. One offline StarRocks cluster FE must not starve backup evaluation for all other clusters.
- *Treating every `PENDING`/`RUNNING` job as orphaned:* Rejected. The tick is a separate process from the API server's `ThreadBackend`; it would double-run pending jobs and finish live jobs early, releasing their slot.

### Decision 5: Short-lived metadata sessions during StarRocks queries
Per `AGENTS.md`, metadata database sessions are committed/closed before opening network connections to StarRocks to check `SHOW BACKUP`/`SHOW RESTORE`, and re-opened only to write the updated job status and history events.

### Decision 6: Heartbeat-based job liveness
`Job` gains a nullable `heartbeat_at`. `ThreadBackend._run_job` starts a daemon thread that updates it every `STARROCKS_BR_JOB_HEARTBEAT_SECONDS` (default 30) in its own short session while the handler runs, and stops it when the handler returns. `mark_running` also sets it. A job is stale when `heartbeat_at` (or `started_at` if null; `created_at` for `PENDING`) is older than `STARROCKS_BR_JOB_STALE_SECONDS` (default 180; configuration MUST reject a value below 3x the heartbeat interval). Reconciliation claims a stale job with a single conditional `UPDATE ... WHERE id = :id AND status = :old AND <still stale>`; a rowcount of 0 means another tick or a live owner got there first, and the job is skipped.

*Rationale & Alternatives Considered:*
- *Heartbeat piggybacked on `on_progress`:* Rejected. Non-polling handlers (`schedule_cleanup`) never call it, and it would only fire on StarRocks state changes.
- *Owner id (hostname:pid) only:* Rejected. A pid cannot be checked across hosts/containers; a time-based lease works with any backend and matches the `scheduler_lock` approach.

## Risks / Trade-offs

- **[Risk] Long backup delays container shutdown**  
  *Mitigation:* Worker threads hold the cluster-level slot (`concurrency.reserve_job_slot`), not the tick-level `scheduler_lock`. If the external orchestrator sends `SIGTERM`, Python's signal handler triggers a graceful teardown.
- **[Risk] Process crash leaves stale lock**  
  *Mitigation:* Locks carry `expires_at = acquired_at + STARROCKS_BR_SCHEDULER_LOCK_TIMEOUT_SECONDS` (default 300s). The atomic conditional update reclaims any expired lock on the subsequent tick and logs a warning.
- **[Risk] Health check overhead on metadata store**  
  *Mitigation:* `GET /health` only queries the single row of `scheduler_lock` (`SELECT last_tick_at FROM scheduler_lock WHERE id = 1`), maintaining fast response times without lock contention.
- **[Risk] `api-scheduling`'s existing "one-shot never selected by run-due" scenario read as blocking expiry**  
  *Mitigation:* that scenario governs only recurring backup submission (`run_due_schedules`), not the separate `expire_due_schedules` path this tick also invokes; corrected in this change's `specs/api-scheduling/spec.md` to say so explicitly, so the merged main spec is unambiguous once both changes archive.
- **[Risk] Heartbeat write failure marks a live job stale**  
  *Mitigation:* the stale threshold is at least 3x the heartbeat interval, so isolated failed writes are tolerated; the `SHOW BACKUP`/`SHOW RESTORE` check still leaves a job in `RUNNING` if StarRocks reports it active.
