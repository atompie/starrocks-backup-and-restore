## Purpose

Provides an out-of-process command-line interface for executing single scheduler ticks with concurrency locking, orphaned-job reconciliation, schedule evaluation, and liveness reporting.

## ADDED Requirements

### Requirement: Scheduler tick CLI invocation and lifecycle
The system SHALL provide a CLI command (`starrocks-br-scheduler tick`, also executable via `python -m starrocks_br.cli.scheduler tick`) that executes exactly one scheduler tick per invocation and exits without entering a resident background loop. On each invocation, the command SHALL attempt to acquire the singleton scheduler lock before performing any schedule evaluation or job reconciliation. If lock acquisition succeeds, the command SHALL execute orphaned job reconciliation, evaluate due recurring schedules, evaluate one-shot schedule expiry, and record the tick completion timestamp. The command SHALL release the scheduler lock in a finally block regardless of success or failure of the tick's operations, and SHALL wait for any in-process worker threads dispatched during the tick to complete before exiting. On successful execution, the command SHALL exit with status code 0.

#### Scenario: Successful scheduler tick execution
- **WHEN** the operator or cron invokes `starrocks-br-scheduler tick` and the lock is free
- **THEN** the command reconciles orphaned jobs, triggers due schedules, expires eligible one-shot schedules, updates the last tick timestamp, releases the lock, and exits with code 0

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

### Requirement: Orphaned job reconciliation on tick startup
Upon successfully acquiring the scheduler lock, the command SHALL reconcile orphaned jobs before evaluating due schedules or expiry. For jobs with status `PENDING`, the system SHALL re-enqueue them onto their configured execution backend. For jobs with status `RUNNING`, the system SHALL query StarRocks for the corresponding `SHOW BACKUP` or `SHOW RESTORE` status using the job's label. If StarRocks reports the operation as `FINISHED`, the system SHALL transition the job to `SUCCESS`, record its references, append a reconciliation event to history, and complete the cluster's concurrency slot. If StarRocks reports `CANCELLED` or the operation is not found, the system SHALL transition the job to `FAILED`, append a reconciliation event, and complete the concurrency slot. If StarRocks reports the operation is still active, the system SHALL leave the job in `RUNNING`. If the target cluster is unreachable, the system SHALL log a warning and skip that job without failing the tick.

#### Scenario: Re-enqueue pending jobs
- **WHEN** a scheduler tick begins and finds jobs in `PENDING` status in the metadata store
- **THEN** the system re-enqueues those jobs onto their designated backend for execution

#### Scenario: Reconcile finished running job
- **WHEN** a scheduler tick finds a `RUNNING` backup job whose StarRocks `SHOW BACKUP` reports `FINISHED`
- **THEN** the system marks the job `SUCCESS`, records its backup references, appends a reconciliation history event, and releases the cluster's concurrency slot

#### Scenario: Reconcile cancelled running job
- **WHEN** a scheduler tick finds a `RUNNING` backup job whose StarRocks `SHOW BACKUP` reports `CANCELLED`
- **THEN** the system marks the job `FAILED`, appends a reconciliation history event, and releases the cluster's concurrency slot

#### Scenario: Active StarRocks job remains running
- **WHEN** a scheduler tick finds a `RUNNING` backup job whose StarRocks `SHOW BACKUP` is still actively in progress
- **THEN** the system leaves the job in `RUNNING` status without modifying its state or failing the tick

#### Scenario: Unreachable cluster is skipped with warning
- **WHEN** a scheduler tick encounters a `RUNNING` job on a cluster whose StarRocks endpoint cannot be reached
- **THEN** the system logs a warning detailing the connection error, leaves the job in `RUNNING`, and continues reconciling remaining jobs

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
