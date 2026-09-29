## Purpose

Provides an out-of-process command-line interface for executing single scheduler ticks with concurrency locking, stale-job reconciliation, schedule evaluation, and liveness reporting.

## Requirements

### Requirement: Scheduler tick CLI invocation and lifecycle
The system SHALL provide a CLI command (`starrocks-br-scheduler tick`, also executable via `python -m starrocks_br.cli.scheduler tick`) that executes exactly one scheduler tick per invocation and exits without entering a resident background loop. On each invocation, the command SHALL attempt to acquire the singleton scheduler lock before performing any schedule evaluation or job reconciliation. If lock acquisition succeeds, the command SHALL execute stale job reconciliation, evaluate due recurring schedules, evaluate one-shot schedule expiry, and record the tick completion timestamp. The command SHALL release the scheduler lock in a finally block regardless of success or failure of the tick's operations, and SHALL wait for any in-process worker threads dispatched during the tick to complete before exiting. On successful execution, the command SHALL exit with status code 0.

#### Scenario: Successful scheduler tick execution
- **WHEN** the operator or cron invokes `starrocks-br-scheduler tick` and the lock is free
- **THEN** the command reconciles stale jobs, triggers due schedules, expires eligible one-shot schedules, updates the last tick timestamp, releases the lock, and exits with code 0

#### Scenario: Worker threads complete before process exit
- **WHEN** a scheduler tick triggers a due schedule using an in-process thread execution backend
- **THEN** the scheduler lock is released upon dispatch completion, the command waits for the worker threads to finish executing the backup job, and the process terminates with code 0

#### Scenario: Lock released on unhandled error
- **WHEN** an unhandled exception occurs during schedule evaluation or reconciliation
- **THEN** the command releases the scheduler lock in a finally block and exits with a non-zero error code

### Requirement: Singleton scheduler lock and contention handling
The system SHALL ensure that at most one scheduler tick executes at any time across the entire system by coordinating through a singleton `scheduler_lock` row in the metadata store. Lock acquisition SHALL be performed using an atomic conditional update that succeeds only when no active lock exists or the existing lock lease has expired. If the lock is currently held by an active process whose lease has not expired, the command SHALL log an alert to stderr stating that the scheduler is already running, SHALL exit immediately with status code 75 (`EX_TEMPFAIL`), and SHALL NOT evaluate due schedules, expire schedules, or modify any job state. If the existing lock has expired beyond `STARROCKS_BR_SCHEDULER_LOCK_TIMEOUT_SECONDS`, the command SHALL reclaim the lock, log a warning indicating that a stale lock was recovered, and proceed with the tick.

#### Scenario: Lock contention exits with EX_TEMPFAIL
- **WHEN** a scheduler tick is invoked while another active tick process holds the unexpired lock
- **THEN** the command outputs "scheduler already running" to stderr, does not evaluate schedules or jobs, and exits immediately with status code 75

#### Scenario: Stale lock recovery
- **WHEN** a scheduler tick is invoked and finds a lock whose lease expired because the previous holder terminated unexpectedly
- **THEN** the command atomically reclaims the lock, logs a warning indicating recovery of a stale lock, and proceeds with tick execution

#### Scenario: Safe lock release
- **WHEN** a scheduler tick process completes its evaluation
- **THEN** it releases the lock only if the current lock holder matches its own identity, preventing accidental clearance of a reclaimed lock

### Requirement: Stale job reconciliation on tick startup
Upon successfully acquiring the scheduler lock, the command SHALL reconcile stale jobs before evaluating due schedules or expiry. A job is stale when its liveness timestamp is older than `STARROCKS_BR_JOB_STALE_SECONDS`: `heartbeat_at` for a `RUNNING` job (falling back to `started_at` when null), `created_at` for a `PENDING` job. The system SHALL NOT modify a job that is not stale, regardless of which process owns it. Each job SHALL be claimed with an atomic conditional update so that concurrent claimants cannot both act on it. A stale `PENDING` job SHALL be re-enqueued onto its configured backend. For a stale `RUNNING` backup or restore job, the system SHALL query StarRocks `SHOW BACKUP` or `SHOW RESTORE` for the job's operation: if it is still in progress the job SHALL be left `RUNNING`; in every other case (`FINISHED`, `CANCELLED`, lost/not found, or no recorded label) the job SHALL be transitioned to `FAILED` with a `FAILED` history event, and a backup's cluster concurrency slot SHALL be released. The system SHALL NOT promote a stale backup or restore job to `SUCCESS`, even when StarRocks reports `FINISHED`, because its backup references (or remaining restore steps) were held only by the terminated process; the failure message SHALL name the unregistered snapshot when StarRocks reports a backup `FINISHED`. A stale `RUNNING` job of any other type SHALL be transitioned to `FAILED` with the reason in its error message. If the target cluster is unreachable, the system SHALL log a warning and skip that job without failing the tick.

#### Scenario: Live running job is left alone
- **WHEN** a scheduler tick finds a `RUNNING` job whose `heartbeat_at` is within the stale threshold (for example one executing in the API server's thread pool)
- **THEN** the system does not query StarRocks for it, does not change its status and does not release its concurrency slot

#### Scenario: Fresh pending job is not re-enqueued
- **WHEN** a scheduler tick finds a `PENDING` job created within the stale threshold
- **THEN** the system leaves it untouched

#### Scenario: Re-enqueue stale pending job
- **WHEN** a scheduler tick finds a `PENDING` job older than the stale threshold
- **THEN** the system claims it and re-enqueues it onto its designated backend

#### Scenario: Finished stale backup is failed, not promoted
- **WHEN** a scheduler tick finds a stale `RUNNING` backup job whose StarRocks `SHOW BACKUP` reports `FINISHED`
- **THEN** the system marks the job `FAILED` (not `SUCCESS`), records no backup references, appends a `FAILED` history event naming the unregistered snapshot, and releases the cluster's concurrency slot

#### Scenario: Cancelled stale backup fails
- **WHEN** a scheduler tick finds a stale `RUNNING` backup job whose StarRocks `SHOW BACKUP` reports `CANCELLED`, or no matching operation
- **THEN** the system marks the job `FAILED`, appends a `FAILED` history event, and releases the cluster's concurrency slot

#### Scenario: Stale restore is failed
- **WHEN** a scheduler tick finds a stale `RUNNING` restore job whose StarRocks `SHOW RESTORE` is not in progress
- **THEN** the system marks the job `FAILED` and appends a `FAILED` restore history event

#### Scenario: StarRocks operation still active
- **WHEN** a scheduler tick finds a stale `RUNNING` backup job whose StarRocks `SHOW BACKUP` is still in progress
- **THEN** the system leaves the job in `RUNNING` without failing the tick

#### Scenario: Stale job without a label fails
- **WHEN** a scheduler tick finds a stale `RUNNING` backup job with no recorded label
- **THEN** the system marks it `FAILED` with a history event and releases the slot

#### Scenario: Stale non-backup job fails
- **WHEN** a scheduler tick finds a stale `RUNNING` `schedule_cleanup` job
- **THEN** the system marks it `FAILED` with the reason in its error message

#### Scenario: Unreachable cluster is skipped with warning
- **WHEN** a scheduler tick encounters a stale `RUNNING` job on a cluster whose StarRocks endpoint cannot be reached
- **THEN** the system logs a warning, leaves the job in `RUNNING`, and continues reconciling remaining jobs

#### Scenario: Concurrent claim
- **WHEN** two claimants attempt to reconcile the same stale job
- **THEN** exactly one conditional update succeeds and the other skips the job

### Requirement: Job heartbeat
While a job's handler runs, the job backend SHALL update the job's `heartbeat_at` every `STARROCKS_BR_JOB_HEARTBEAT_SECONDS` (default 30) using its own short-lived metadata session, independent of handler progress callbacks, and SHALL stop when the handler returns. `STARROCKS_BR_JOB_STALE_SECONDS` (default 180) SHALL be at least three times the heartbeat interval; a lower value SHALL be rejected at configuration time.

#### Scenario: Heartbeat advances during a long job
- **WHEN** a job handler runs longer than the heartbeat interval without progress callbacks
- **THEN** `heartbeat_at` is updated at each interval

#### Scenario: Heartbeat stops on completion
- **WHEN** a job handler returns or raises
- **THEN** the heartbeat thread stops and no further updates occur

#### Scenario: Invalid stale threshold rejected
- **WHEN** `STARROCKS_BR_JOB_STALE_SECONDS` is less than three times `STARROCKS_BR_JOB_HEARTBEAT_SECONDS`
- **THEN** configuration loading fails with a clear error

### Requirement: Scheduler liveness observability via health endpoint
Upon successful completion of all tick operations, the command SHALL record the current timestamp as `last_tick_at` in the metadata store. The system SHALL expose this timestamp in the unauthenticated `GET /health` endpoint under `scheduler.last_tick_at`. If a tick exits early due to lock contention (`EX_TEMPFAIL`), the system SHALL NOT update `last_tick_at`.

#### Scenario: Health endpoint reports last tick timestamp
- **WHEN** a client performs a `GET /health` request after one or more successful scheduler ticks
- **THEN** the response includes `scheduler.last_tick_at` containing the ISO-8601 timestamp of the most recent successful tick

#### Scenario: Contention does not update last tick timestamp
- **WHEN** a scheduler tick exits with status 75 due to lock contention
- **THEN** `scheduler.last_tick_at` remains unchanged in the metadata store

#### Scenario: Initial health endpoint before any tick
- **WHEN** a client performs a `GET /health` request before any scheduler tick has ever completed
- **THEN** the response includes `scheduler.last_tick_at` as null
