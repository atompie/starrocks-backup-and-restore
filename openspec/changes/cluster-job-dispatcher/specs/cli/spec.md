## MODIFIED Requirements

### Requirement: Scheduler tick CLI invocation and lifecycle
The system SHALL provide a CLI command (`starrocks-br-scheduler tick`, also executable via `python -m starrocks_br.cli.scheduler tick`) that executes exactly one scheduler tick per invocation and exits without entering a resident background loop. On each invocation, the command SHALL attempt to acquire the singleton scheduler lock before performing any schedule evaluation or job reconciliation. If lock acquisition succeeds, the command SHALL execute stale job reconciliation, evaluate due recurring schedules, evaluate one-shot schedule expiry, admit queued jobs (the dispatch step, at most one job per cluster and only for clusters with no `RUNNING` job), and record the tick completion timestamp. The command SHALL release the scheduler lock in a finally block regardless of success or failure of the tick's operations, and SHALL wait for any in-process worker threads dispatched during the tick to complete before exiting. On successful execution, the command SHALL exit with status code 0.

#### Scenario: Successful scheduler tick execution
- **WHEN** the operator or cron invokes `starrocks-br-scheduler tick` and the lock is free
- **THEN** the command reconciles stale jobs, triggers due schedules, expires eligible one-shot schedules, admits queued jobs, updates the last tick timestamp, releases the lock, and exits with code 0

#### Scenario: Worker threads complete before process exit
- **WHEN** a scheduler tick triggers a due schedule using an in-process thread execution backend
- **THEN** the scheduler lock is released upon dispatch completion, the command waits for the worker threads to finish executing the backup job, and the process terminates with code 0

#### Scenario: Lock released on unhandled error
- **WHEN** an unhandled exception occurs during schedule evaluation or reconciliation
- **THEN** the command releases the scheduler lock in a finally block and exits with a non-zero error code

#### Scenario: Jobs created in the tick can start in the same tick
- **WHEN** a tick submits a due schedule's job on a cluster with no `RUNNING` job
- **THEN** the same tick's dispatch step admits that job and the command waits for its worker before exiting

## REMOVED Requirements

### Requirement: Stale job reconciliation on tick startup
**Reason**: It re-enqueued stale `PENDING` jobs. Under the cluster job dispatcher a `PENDING` job is a job waiting for its cluster lane, and only the dispatch step starts jobs.
**Migration**: Replaced by "Stale RUNNING job reconciliation on tick startup" below, which keeps every `RUNNING` rule unchanged.

## ADDED Requirements

### Requirement: Stale RUNNING job reconciliation on tick startup
Upon successfully acquiring the scheduler lock, the command SHALL reconcile stale jobs before evaluating due schedules or expiry. A job is stale when its liveness timestamp is older than `STARROCKS_BR_JOB_STALE_SECONDS`: `heartbeat_at` for a `RUNNING` job (falling back to `started_at` when null). The system SHALL NOT modify a job that is not stale, regardless of which process owns it. Each job SHALL be claimed with an atomic conditional update so that concurrent claimants cannot both act on it. A `PENDING` job is a job waiting for its turn and SHALL NOT be treated as stale or re-enqueued by reconciliation, regardless of its age. For a stale `RUNNING` backup or restore job, the system SHALL query StarRocks `SHOW BACKUP` or `SHOW RESTORE` for the job's operation: if it is still in progress the job SHALL be left `RUNNING`; in every other case (`FINISHED`, `CANCELLED`, lost/not found, or no recorded label) the job SHALL be transitioned to `FAILED` with a `FAILED` history event, and a backup's cluster concurrency slot SHALL be released. The system SHALL NOT promote a stale backup or restore job to `SUCCESS`, even when StarRocks reports `FINISHED`, because its backup references (or remaining restore steps) were held only by the terminated process; the failure message SHALL name the unregistered snapshot when StarRocks reports a backup `FINISHED`. A stale `RUNNING` job of any other type SHALL be transitioned to `FAILED` with the reason in its error message. If the target cluster is unreachable, the system SHALL log a warning and skip that job without failing the tick.

#### Scenario: Live running job is left alone
- **WHEN** a scheduler tick finds a `RUNNING` job whose `heartbeat_at` is within the stale threshold (for example one executing in the API server's thread pool)
- **THEN** the system does not query StarRocks for it, does not change its status and does not release its concurrency slot

#### Scenario: Pending job is never re-enqueued by reconciliation
- **WHEN** a scheduler tick finds a `PENDING` job of any age
- **THEN** the system leaves it untouched; only the dispatch step starts it

#### Scenario: Admitted job whose worker never started is failed
- **WHEN** a job was admitted (`RUNNING`, heartbeat set at admission) but its worker died before writing any heartbeat and the stale threshold has passed
- **THEN** a later tick treats it as a stale `RUNNING` job and fails it under the rules above

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
