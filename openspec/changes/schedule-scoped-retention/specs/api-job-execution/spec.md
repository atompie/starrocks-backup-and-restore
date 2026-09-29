## MODIFIED Requirements

### Requirement: Submitting an operation returns immediately with a job
The system SHALL accept requests to start a restore operation against a registered
cluster, SHALL create a job record in PENDING state, SHALL return
an HTTP 202 response containing the job id and status without waiting for the operation to
complete, and SHALL begin executing the operation asynchronously. Backup Jobs created by schedule
submission SHALL use the same asynchronous job lifecycle and SHALL record references for every
database/table covered once each database's StarRocks operation reaches `FINISHED`.

#### Scenario: Manual backup submission routes are retired
- **WHEN** an authenticated client submits a request to `/backup/manual/full/cluster/{cluster_id}`
  or `/backup/manual/incremental/cluster/{cluster_id}`
- **THEN** the request is rejected with HTTP 404 and creates no job; clients create a one-shot
  schedule for an immediate full backup or a recurring schedule for an incremental backup

#### Scenario: Manual prune submission route is retired
- **WHEN** an authenticated client submits a request to `/backup/manual/prune/cluster/{cluster_id}`
- **THEN** the request is rejected with HTTP 404 and creates no job; retention is enforced
  automatically by schedules

#### Scenario: Submission against an unknown cluster
- **WHEN** an authenticated client submits any job against a cluster id that is not registered
- **THEN** the system responds with HTTP 404 and does not create a job

### Requirement: Each job-submission endpoint validates a request schema scoped to its own fields
The system SHALL reject a job-submission request that includes a field not used by that specific
endpoint with HTTP 422, rather than silently accepting and ignoring it.

#### Scenario: Foreign field rejected
- **WHEN** an authenticated client submits a restore request that includes a field not used
  by the restore endpoint (e.g. `retention` or `expire_after_days`)
- **THEN** the system responds with HTTP 422 and does not create a job

### Requirement: A job's execution history can be retrieved
The system SHALL expose an endpoint to retrieve the append-only execution history of a backup,
restore, or retention job, returned as an ordered list from oldest to newest, each entry carrying the
status recorded at that point in time, a timestamp, and an optional message and detail payload. History
entries are never modified or removed once recorded; the endpoint always reflects every entry
recorded so far for that job. A backup or retention job that ends in status `FAILED` SHALL always have at least
one `FAILED` history entry, carrying the error message that caused the failure, regardless of
whether the failure occurred while a StarRocks operation was submitted or polled, or at any
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

#### Scenario: Retrieving history for a retention job
- **WHEN** an authenticated client requests the history of a retention job
- **THEN** the system responds with HTTP 200 and a time-ordered list of that job's recorded
  status entries (`RETENTION_STARTED`, `SNAPSHOT_DROPPED`, `RETENTION_FINISHED`, or `ERROR`/`FAILED`)

## REMOVED Requirements

### Requirement: Prune requests specify exactly one pruning strategy
**Reason**: Manual inventory-scoped prune violates schedule independence (`SPEC.md` §11) and history immutability (`SPEC.md` §13-14). It is replaced by automated schedule-scoped retention.
**Migration**: Configure `retention` on recurring full schedules via `POST /backup/schedules/cluster/{cluster_id}`.
