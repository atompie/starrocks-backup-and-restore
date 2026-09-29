# api-job-execution Specification

## Purpose

Lets clients start restore and prune operations against a registered cluster as asynchronous jobs, observe job progress without blocking on the HTTP request, and lets operators choose (per job or by default) which execution backend runs the work. Backup jobs are created through recurring or one-shot schedules as specified by `api-scheduling`; the manual full and incremental backup submission routes are retired.

## Requirements

### Requirement: Submitting an operation returns immediately with a job
The system SHALL accept requests to start a restore or prune operation against a registered
cluster, SHALL create a job record in PENDING state, SHALL return
an HTTP 202 response containing the job id and status without waiting for the operation to
complete, and SHALL leave the job PENDING until a scheduler tick starts it. Backup Jobs created by
schedule submission SHALL use the same queued lifecycle and SHALL record references for every
database/table covered once each database's StarRocks operation reaches `FINISHED`. The system
SHALL NOT expose manual full or incremental backup submission routes.

#### Scenario: Manual backup submission routes are retired
- **WHEN** an authenticated client submits a request to `/backup/manual/full/cluster/{cluster_id}`
  or `/backup/manual/incremental/cluster/{cluster_id}`
- **THEN** the system responds with HTTP 404 and creates no job; clients create a one-shot schedule
  for an immediate full backup or a recurring schedule for an incremental backup

#### Scenario: Submission against an unknown cluster
- **WHEN** an authenticated client submits any job against a cluster id that is not registered
- **THEN** the system responds with HTTP 404 and does not create a job

#### Scenario: Schedule cleanup is submitted as a tracked job
- **WHEN** a schedule deletion is accepted
- **THEN** the system creates a `schedule_cleanup` job that can be polled through the standard job
  endpoint and reports SUCCESS or FAILED when cleanup completes

#### Scenario: Deleting a backup removes restore jobs that used it
- **WHEN** schedule cleanup deletes a backup job used as the source of one or more restore jobs
- **THEN** those restore job records and their histories are deleted with the backup job

#### Scenario: A submitted job waits for a scheduler tick
- **WHEN** an authenticated client submits a restore and no scheduler tick has run since
- **THEN** the response is HTTP 202 with status `PENDING` and the job stays `PENDING` until a tick admits it

### Requirement: Jobs on one cluster run one at a time, in priority order
The system SHALL start queued jobs only from the scheduler tick's dispatch step. For each cluster
it SHALL admit at most one job per tick, and only when no job is `RUNNING` on that cluster,
regardless of job type. Among that cluster's `PENDING` jobs it SHALL admit restore jobs before
backup jobs (`backup_full`, `backup_incremental`) and backup jobs before every other job type,
oldest first within a priority. Admission SHALL transition the job from `PENDING` to `RUNNING` (setting
its start time and heartbeat) before the job is handed to its execution backend. Jobs on different clusters
SHALL be admitted independently and MAY run concurrently. A job that has to wait for its cluster SHALL
remain `PENDING` and SHALL NOT fail because the cluster is busy.

#### Scenario: Two jobs on one cluster run one after another
- **WHEN** two `PENDING` jobs exist for the same cluster and none is `RUNNING`
- **THEN** a tick admits only the higher-priority (or older) one, the other stays `PENDING`, and it is
  admitted on a later tick after the first is no longer `RUNNING`

#### Scenario: Jobs on different clusters run in parallel
- **WHEN** each of two clusters has a `PENDING` job and neither cluster has a `RUNNING` job
- **THEN** a single tick admits one job on each cluster

#### Scenario: Restore is admitted before backup
- **WHEN** a cluster has a `PENDING` backup and a `PENDING` restore and no `RUNNING` job
- **THEN** the restore is admitted first

#### Scenario: A busy cluster admits nothing
- **WHEN** a cluster has a `RUNNING` job and `PENDING` jobs
- **THEN** the tick admits none of them and leaves them `PENDING` without failing them

#### Scenario: Overlapping ticks do not double-admit
- **WHEN** two dispatch passes consider the same `PENDING` job at the same time
- **THEN** exactly one conditional update succeeds, only that pass enqueues the job, and the cluster
  ends with one `RUNNING` job

### Requirement: Restore submission cannot race with source backup cleanup
When a restore request identifies a source backup whose schedule is pending deletion, the system
SHALL reject the request with HTTP 409 before creating a Restore Job. Resolving the source backup
and creating the Restore Job SHALL be coordinated with schedule deletion so either the restore job
is visible to deletion validation, or the pending-deletion state is visible to restore submission.

#### Scenario: Restore submission is blocked after cleanup is accepted
- **WHEN** an authenticated client requests a restore from a backup whose schedule is pending
  deletion
- **THEN** the system responds with HTTP 409 and creates no Restore Job

### Requirement: Each job-submission endpoint validates a request schema scoped to its own fields
The system SHALL reject a job-submission request that includes a field not used by that specific
endpoint with HTTP 422, rather than silently accepting and ignoring it.

#### Scenario: Foreign field rejected
- **WHEN** an authenticated client submits a restore request that includes a field only used
  by another job type (e.g. `keep_last`, which only prune uses)
- **THEN** the system responds with HTTP 422 and does not create a job

### Requirement: Restore requests accept at most one of group or table
The system SHALL reject a restore request that specifies both `group_id` and `table` with HTTP 422
before any job is created. A restore request specifying neither SHALL be accepted and SHALL
restore every table present in the target backup. A restore request that specifies `table` SHALL
also specify `database` (identifying which database the bare table name belongs to), and the
system SHALL reject a `table` request missing `database` with HTTP 422 before any job is created;
a restore request that specifies `group_id` SHALL NOT require `database`, since the group's own
table memberships already identify each table's database. The system SHALL determine which
repository holds the target backup from that backup's own recorded history rather than from any
client-supplied field.

#### Scenario: Both group and table specified
- **WHEN** an authenticated client submits a restore request specifying both `group_id` and
  `table`
- **THEN** the system responds with HTTP 422 and does not create a job

#### Scenario: Neither group nor table specified
- **WHEN** an authenticated client submits a restore request specifying neither `group_id` nor
  `table`
- **THEN** the system responds with HTTP 202 and creates a job that restores every table present
  in the target backup

#### Scenario: Table specified without database
- **WHEN** an authenticated client submits a restore request specifying `table` but not
  `database`
- **THEN** the system responds with HTTP 422 and does not create a job

#### Scenario: Table specified with database
- **WHEN** an authenticated client submits a restore request specifying both `table` and
  `database`
- **THEN** the system responds with HTTP 202 and creates a job that restores that table from the
  specified database

### Requirement: Prune requests specify exactly one pruning strategy
The system SHALL reject a prune request that specifies zero, or more than one, of `keep_last`,
`older_than`, `snapshot`, `snapshots` with HTTP 422 before any job is created. Every prune request
SHALL specify `group_id`; the system SHALL reject a prune request missing `group_id` (or supplying
a value that cannot be parsed as an inventory group id) with HTTP 422 before any job is created,
so that pruning is always scoped to one inventory group's backups and can never target every
backup in a repository at once. The system SHALL synchronously verify that an inventory group with
that id exists on the target cluster before creating the job, responding with HTTP 404 without
creating a job if it does not. The system SHALL determine which repository each candidate snapshot
belongs to from that snapshot's own recorded backup history rather than from any client-supplied
field.

#### Scenario: No strategy specified
- **WHEN** an authenticated client submits a prune request specifying none of `keep_last`,
  `older_than`, `snapshot`, `snapshots`
- **THEN** the system responds with HTTP 422 and does not create a job

#### Scenario: Multiple strategies specified
- **WHEN** an authenticated client submits a prune request specifying more than one of
  `keep_last`, `older_than`, `snapshot`, `snapshots`
- **THEN** the system responds with HTTP 422 and does not create a job

#### Scenario: No group specified
- **WHEN** an authenticated client submits a prune request with no inventory group id specified,
  or one that cannot be parsed as an id
- **THEN** the system responds with HTTP 422 before any job is created, rather than accepting the
  request and failing asynchronously

#### Scenario: Prune submitted with an unknown group
- **WHEN** an authenticated client submits a prune request naming an inventory group id that does
  not exist on the target cluster
- **THEN** the system responds with HTTP 404, does not create a job, and no asynchronous failure
  is produced

### Requirement: Job status and progress can be polled
The system SHALL expose an endpoint to retrieve a job's current status (PENDING, RUNNING, SUCCESS,
FAILED), timestamps (created, started, finished), and, when the underlying StarRocks operation
reports a numeric progress indicator, a progress value reflecting the most recently observed
value. The returned job record SHALL also include, when applicable: the id of the schedule that
submitted it (null for a job submitted directly rather than by a schedule), the inventory group id
it targeted (null for a job type that does not carry one), the id of the full backup job an
incremental backup job was based on (null for any other job type), and, once the job has finished
successfully, a result summary.

#### Scenario: Progress is available mid-operation
- **WHEN** a client polls a job that is RUNNING and StarRocks currently reports a numeric progress percentage for that phase
- **THEN** the system returns status RUNNING together with the most recently observed progress percentage

#### Scenario: Progress is unavailable for a phase
- **WHEN** a client polls a job that is RUNNING during a phase where StarRocks reports no numeric progress
- **THEN** the system returns status RUNNING with started_at set and no progress percentage, rather than an error

#### Scenario: Completed job reports final state
- **WHEN** a client polls a job that has finished
- **THEN** the system returns status SUCCESS or FAILED, with started_at and finished_at both set, and an error message when FAILED

#### Scenario: Polling an unknown job
- **WHEN** a client polls a job id that does not exist
- **THEN** the system responds with HTTP 404

#### Scenario: Polling a schedule-submitted job reports its schedule id
- **WHEN** a client polls a job that a schedule (recurring or one-shot) submitted
- **THEN** the system returns that schedule's id as the job's `schedule_id`

#### Scenario: Polling a directly-submitted job reports no schedule id
- **WHEN** a client polls a job that was submitted directly (not through a schedule)
- **THEN** the system returns `schedule_id` as null

#### Scenario: Polling an incremental backup job reports its baseline job id
- **WHEN** a client polls an incremental backup job whose baseline full backup has been resolved
- **THEN** the system returns that full backup job's id as the job's `baseline_job_id`

#### Scenario: Polling a completed job reports a result summary
- **WHEN** a client polls a job that finished successfully
- **THEN** the system returns a result summary describing the operation's outcome

### Requirement: Backup job history can be listed for a cluster
The system SHALL expose an endpoint to list jobs for a registered cluster, ordered most recently
created first, each with its current status (PENDING, RUNNING, SUCCESS, FAILED), timestamps
(created, started, finished), and, when the underlying StarRocks operation reports a numeric
progress indicator, a progress value reflecting the most recently observed value. By default the
listing SHALL be scoped to backup jobs (`backup_full` and `backup_incremental`); the endpoint
SHALL accept an optional job type filter to include a different job type instead, and an optional
status filter to narrow the listing to jobs currently in a given status. The endpoint SHALL accept
an optional `job_id` filter to narrow the listing to the single job with that id, an optional
`group_id` filter to narrow the listing to jobs submitted for that inventory group, and an
optional `schedule_id` filter to narrow the listing to jobs submitted by that schedule; a
`group_id` or `schedule_id` filter SHALL only match jobs that carry that value, so jobs of other
types, or jobs submitted directly rather than by a schedule, are excluded when the corresponding
filter is applied. The endpoint SHALL accept `limit` and `offset` parameters to page through
results, and SHALL respond with HTTP 404 without returning any listing if the cluster id is not
registered.

#### Scenario: Listing backup history for a cluster with prior backups
- **WHEN** an authenticated client lists backup history for a registered cluster that has one or
  more prior backup jobs
- **THEN** the system responds with HTTP 200 and a list of those jobs, ordered most recently
  created first, each including its status, timestamps, and progress when available

#### Scenario: Listing backup history for a cluster with no prior backups
- **WHEN** an authenticated client lists backup history for a registered cluster that has no
  backup jobs
- **THEN** the system responds with HTTP 200 and an empty list

#### Scenario: Listing backup history for an unknown cluster
- **WHEN** an authenticated client lists backup history for a cluster id that is not registered
- **THEN** the system responds with HTTP 404

#### Scenario: Filtering backup history by status
- **WHEN** an authenticated client lists backup history for a registered cluster with a status
  filter applied
- **THEN** the system responds with HTTP 200 and only jobs currently in that status

#### Scenario: Paginating backup history
- **WHEN** an authenticated client lists backup history for a registered cluster with `limit` and
  `offset` parameters
- **THEN** the system responds with HTTP 200 and returns at most `limit` jobs, skipping the first
  `offset` jobs in the most-recently-created-first ordering

#### Scenario: Filtering backup history by job id
- **WHEN** an authenticated client lists backup history for a registered cluster with a `job_id`
  filter applied
- **THEN** the system responds with HTTP 200 and returns only the job with that id, or an empty
  list if no job with that id exists on the cluster

#### Scenario: Filtering backup history by group id
- **WHEN** an authenticated client lists backup history for a registered cluster with a
  `group_id` filter applied
- **THEN** the system responds with HTTP 200 and returns only jobs that were submitted for that
  inventory group, excluding jobs of a type that does not carry a group id and jobs submitted for
  a different group

#### Scenario: Filtering backup history by group id excludes jobs submitted before the filter was supported
- **WHEN** an authenticated client lists backup history for a registered cluster with a
  `group_id` filter applied, and the cluster has jobs that were submitted before group id
  tracking existed
- **THEN** the system responds with HTTP 200 and excludes those older jobs from the filtered
  result, since they have no recorded group id

#### Scenario: Filtering backup history by schedule id
- **WHEN** an authenticated client lists backup history for a registered cluster with a
  `schedule_id` filter applied
- **THEN** the system responds with HTTP 200 and returns only jobs that schedule submitted
  (through run-due or one-shot creation), excluding jobs submitted directly

#### Scenario: Filtering backup history by schedule id excludes jobs submitted before the filter was supported
- **WHEN** an authenticated client lists backup history for a registered cluster with a
  `schedule_id` filter applied, and the cluster has jobs that were submitted before schedule
  linkage existed
- **THEN** the system responds with HTTP 200 and excludes those older jobs from the filtered
  result, since they have no recorded schedule id

### Requirement: A job's execution history can be retrieved
The system SHALL expose an endpoint to retrieve the append-only execution history of a backup or
restore job, returned as an ordered list from oldest to newest, each entry carrying the status
recorded at that point in time, a timestamp, and an optional message and detail payload. History
entries are never modified or removed once recorded; the endpoint always reflects every entry
recorded so far for that job. A backup job that ends in status `FAILED` SHALL always have at least
one `FAILED` history entry, carrying the error message that caused the failure, regardless of
whether the failure occurred while a StarRocks backup operation was submitted or polled, or at any
other point in the job's execution.

#### Scenario: Retrieving history for a completed backup job
- **WHEN** an authenticated client requests the history of a backup job that has finished
- **THEN** the system responds with HTTP 200 and a time-ordered list of that job's recorded
  status entries, ending with its final `SUCCESS` or `FAILED` entry

#### Scenario: Retrieving history for a running job
- **WHEN** an authenticated client requests the history of a job that is still `RUNNING`
- **THEN** the system responds with HTTP 200 and the entries recorded so far, without a terminal
  `SUCCESS`/`FAILED` entry

#### Scenario: Retrieving history for an unknown job
- **WHEN** an authenticated client requests the history of a job id that does not exist
- **THEN** the system responds with HTTP 404

#### Scenario: Repeated identical status is not duplicated
- **WHEN** the underlying StarRocks operation reports the same status on consecutive polls
- **THEN** the job's history contains only one entry for that status, not one entry per poll

#### Scenario: A backup job that fails before submitting to StarRocks still records why
- **WHEN** a full or incremental backup job fails for a reason unrelated to StarRocks backup
  submission or polling (for example, no matching tables are found, or recording backup
  references fails)
- **THEN** the job ends with status `FAILED` and its history contains a `FAILED` entry carrying
  the error message that caused the failure

#### Scenario: A backup job that fails to submit to StarRocks still records why
- **WHEN** a full or incremental backup job's StarRocks backup command fails to submit (for
  example, because a snapshot with that name already exists)
- **THEN** the job ends with status `FAILED` and its history contains a `FAILED` entry carrying
  the error message that caused the submission failure, rather than an empty history

### Requirement: A job's backup references can be retrieved
The system SHALL expose an endpoint to retrieve the backup references recorded for a job, returned
as a list, each entry carrying the repository, snapshot label, snapshot timestamp, database,
table, and (when applicable) partition it covers. References are recorded only once the job's
underlying StarRocks backup operation reaches `FINISHED`; a job that has not yet finished, or that
finished as `FAILED`, SHALL have no references. The endpoint SHALL respond with HTTP 404 if the job
id does not exist.

#### Scenario: Retrieving references for a successful backup job
- **WHEN** an authenticated client requests the references of a backup job that finished with
  status `SUCCESS`
- **THEN** the system responds with HTTP 200 and a list of the references recorded for that job

#### Scenario: Retrieving references for a failed backup job
- **WHEN** an authenticated client requests the references of a backup job that finished with
  status `FAILED`
- **THEN** the system responds with HTTP 200 and an empty list

#### Scenario: Retrieving references for a job still running
- **WHEN** an authenticated client requests the references of a backup job that is still `RUNNING`
- **THEN** the system responds with HTTP 200 and an empty list, since references are only recorded
  once the job's StarRocks operation reaches `FINISHED`

#### Scenario: Retrieving references for an unknown job
- **WHEN** an authenticated client requests the references of a job id that does not exist
- **THEN** the system responds with HTTP 404

### Requirement: Job execution backend is selectable with a configured default
The system SHALL execute each submitted job using one of a set of registered execution backends, SHALL use a configured default backend when a request does not specify one, and SHALL allow a request to override the backend for that job as long as the requested backend is enabled on the server. A submitted backend value, whether the cluster's `default_backend` or a per-job override, MUST be one of the recognized backend identifiers `"thread"` or `"job"`; the system SHALL reject any other value with HTTP 422 before any job is created, independent of whether that backend is currently enabled on the server.

#### Scenario: Default backend is used when none is specified
- **WHEN** an authenticated client submits a job without specifying a backend
- **THEN** the system executes the job using the server's configured default backend

#### Scenario: Client overrides the backend for one job
- **WHEN** an authenticated client submits a job specifying an execution backend that is enabled on the server
- **THEN** the system executes that job using the specified backend instead of the default

#### Scenario: Client requests a disabled backend
- **WHEN** an authenticated client submits a job specifying an execution backend that is a recognized identifier but is not enabled on the server
- **THEN** the system rejects the request with a 422 validation error and does not create a job

#### Scenario: Client requests an unrecognized backend value
- **WHEN** an authenticated client submits a job specifying a backend value that is not one of `"thread"` or `"job"`
- **THEN** the system rejects the request with a 422 validation error and does not create a job

### Requirement: Multiple execution backends can be active simultaneously
The system SHALL support more than one execution backend being enabled at the same time on a single server, with independent jobs concurrently executing on different backends.

#### Scenario: Two jobs on two backends run concurrently
- **WHEN** the server has two execution backends enabled and two jobs are submitted, each specifying a different enabled backend
- **THEN** both jobs execute and report status/progress independently, regardless of which backend each was assigned to

### Requirement: Job execution reuses existing backup/restore/prune behavior unchanged
The system SHALL produce the same backup labels, bookkeeping records (persisted in the tool's own
SQLite metastore rather than on the StarRocks side), and StarRocks-side snapshot behavior for a
given backup/restore/prune operation regardless of which execution backend runs it.

#### Scenario: Schedule-submitted full backup uses the selected backend
- **WHEN** the same full backup (same cluster, inventory group, and repository) is submitted via
  the API twice, once to each of two enabled execution backends
- **THEN** the resulting snapshot label, the backup job's own recorded label/repository and
  execution history, and the repository snapshot are equivalent for the same inputs, regardless
  of which backend executed the operation
