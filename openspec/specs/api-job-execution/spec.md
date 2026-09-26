# api-job-execution Specification

## Purpose

Lets clients trigger backup, restore, and prune operations against a registered cluster as asynchronous jobs, observe their progress without blocking on the HTTP request, and lets operators choose (per job or by default) which execution backend runs the work so the same API contract can be served by an in-process thread today and by distributed workers later.

## Requirements

### Requirement: Submitting an operation returns immediately with a job
The system SHALL accept requests to start a full backup, incremental backup, restore, or prune
operation against a registered cluster, SHALL create a job record in PENDING state, SHALL return
an HTTP 202 response containing the job id and status without waiting for the operation to
complete, and SHALL begin executing the operation asynchronously. For a full or incremental backup
request, the system SHALL reject a request that is missing `group_id` (or supplies a value that
cannot be parsed as an inventory group id) with HTTP 422 before any job is created. For a full or
incremental backup request that does supply a `group_id`, the system SHALL synchronously verify
that an inventory group with that id exists on the target cluster before creating the job, and
SHALL respond with HTTP 404 without creating a job if it does not. A full or incremental backup
request SHALL also include a `repository` naming the destination backup repository; the system
SHALL reject a request missing `repository` with HTTP 422 before any job is created, and SHALL
synchronously verify that a repository with that name currently exists on the target cluster
(via the same live StarRocks-side catalog lookup used to list repositories) before creating the
job, responding with HTTP 404 without creating a job if it does not.

#### Scenario: Full backup submission
- **WHEN** an authenticated client submits a full backup request for a registered cluster, an
  inventory group id that exists on that cluster, and a repository that exists on that cluster
- **THEN** the system responds with HTTP 202, a job id, and status PENDING, and the operation
  continues running after the response is sent

#### Scenario: Submission against an unknown cluster
- **WHEN** an authenticated client submits any job against a cluster id that is not registered
- **THEN** the system responds with HTTP 404 and does not create a job

#### Scenario: Full or incremental backup submitted with an unknown group
- **WHEN** an authenticated client submits a full or incremental backup request naming an
  inventory group id that does not exist on the target cluster
- **THEN** the system responds with HTTP 404, does not create a job, and no asynchronous failure
  is produced

#### Scenario: Full or incremental backup submitted with no group
- **WHEN** an authenticated client submits a full or incremental backup request with no inventory
  group id specified, or one that cannot be parsed as an id
- **THEN** the system responds with HTTP 422 before any job is created, rather than accepting the
  request and failing asynchronously

#### Scenario: Full or incremental backup submitted with no repository
- **WHEN** an authenticated client submits a full or incremental backup request with no
  `repository` specified
- **THEN** the system responds with HTTP 422 before any job is created, rather than accepting the
  request and failing asynchronously

#### Scenario: Full or incremental backup submitted with an unknown repository
- **WHEN** an authenticated client submits a full or incremental backup request naming a
  `repository` that does not exist on the target cluster
- **THEN** the system responds with HTTP 404, does not create a job, and no asynchronous failure
  is produced

### Requirement: Each job-submission endpoint validates a request schema scoped to its own fields
The system SHALL reject a job-submission request that includes a field not used by that specific
endpoint with HTTP 422, rather than silently accepting and ignoring it.

#### Scenario: Foreign field rejected
- **WHEN** an authenticated client submits a full backup request that includes a field only used
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
The system SHALL expose an endpoint to retrieve a job's current status (PENDING, RUNNING, SUCCESS, FAILED), timestamps (created, started, finished), and, when the underlying StarRocks operation reports a numeric progress indicator, a progress value reflecting the most recently observed value.

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
SQLite metastore rather than on the StarRocks side), and StarRocks-side snapshot behavior for a job
submitted via the API as the equivalent existing CLI command produces, for any execution backend.

#### Scenario: API-submitted full backup is indistinguishable from a CLI backup
- **WHEN** a full backup is submitted via the API for a group that would otherwise be backed up via the CLI's `backup full` command
- **THEN** the resulting snapshot label, SQLite-backed `backup_history` record (scoped to that cluster), and repository snapshot match what the CLI command would have produced for the same inputs
