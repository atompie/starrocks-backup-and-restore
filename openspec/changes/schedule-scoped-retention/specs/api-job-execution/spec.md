## MODIFIED Requirements

### Requirement: Submitting an operation returns immediately with a job
The system SHALL accept requests to start a restore operation against a registered
cluster, SHALL create a job record in PENDING state, SHALL return
an HTTP 202 response containing the job id and status without waiting for the operation to
complete, and SHALL leave the job PENDING until a scheduler tick starts it. Backup Jobs created by
schedule submission and Retention Jobs created by the scheduler tick SHALL use the same queued
lifecycle, and Backup Jobs SHALL record references for every database/table covered once each
database's StarRocks operation reaches `FINISHED`. The system SHALL NOT expose manual full or
incremental backup submission routes or a manual prune submission route.

#### Scenario: Manual backup submission routes are retired
- **WHEN** an authenticated client submits a request to `/backup/manual/full/cluster/{cluster_id}`
  or `/backup/manual/incremental/cluster/{cluster_id}`
- **THEN** the system responds with HTTP 404 and creates no job; clients create a one-shot schedule
  for an immediate full backup or a recurring schedule for an incremental backup

#### Scenario: Manual prune submission route is retired
- **WHEN** an authenticated client submits a request to `/backup/manual/prune/cluster/{cluster_id}`
- **THEN** the request is rejected with HTTP 404 and creates no job; retention is enforced
  automatically for recurring full-backup schedules

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

## ADDED Requirements

### Requirement: Retention drops only backups that are not protected
The system SHALL run retention for a recurring full-backup schedule over that schedule's own successful
full backups only, keeping the newest `retention` of them, and SHALL drop older ones by dropping their
StarRocks snapshots and setting each backup's references `deleted_at`, never deleting its `Job` or
history rows. It SHALL NOT drop a full backup that is the baseline of an incremental backup in status
`PENDING`, `RUNNING` or `SUCCESS` on the cluster, or that is the source (or the baseline of the source) of
a restore job in status `PENDING` or `RUNNING`. It SHALL re-check these protections immediately before
dropping each backup. A retention job SHALL never change the status of any backup job.

#### Scenario: Oldest non-baseline backup is dropped
- **WHEN** a schedule with `retention` 5 has 6 successful full backups and none is protected
- **THEN** the retention job drops the oldest one's snapshots, sets its references `deleted_at`, and
  leaves its `Job` and history intact

#### Scenario: Baseline of an incremental is spared
- **WHEN** the oldest successful full backup is the baseline of an active or successful incremental backup
- **THEN** it is not dropped even though it is outside the newest `retention`

#### Scenario: Source of a queued restore is spared
- **WHEN** a `PENDING` restore job's source backup is a droppable candidate
- **THEN** the retention job does not drop it

#### Scenario: Schedules keep independent pools
- **WHEN** two schedules target the same inventory group and cluster
- **THEN** retention for one never drops the other's backups

#### Scenario: Drop failure leaves backups untouched
- **WHEN** `DROP SNAPSHOT` fails for a backup
- **THEN** the retention job ends `FAILED` with a `FAILED` history entry, that backup keeps `deleted_at`
  unset, every backup job stays `SUCCESS`, and a later tick retries it

### Requirement: Retention runs within a time limit
The system SHALL stop a retention job from starting new snapshot drops once
`STARROCKS_BR_RETENTION_MAX_SECONDS` has elapsed since it started. It SHALL end such a job normally with a
`RETENTION_FINISHED` history entry that records the deadline was reached, and the remaining droppable
backups SHALL be handled by a retention job submitted by a later tick.

#### Scenario: Deadline reached mid-run
- **WHEN** the deadline elapses while a retention job still has droppable backups
- **THEN** it drops no further backups, ends `SUCCESS` with a history entry noting the deadline, and a later
  tick submits a new retention job for the rest

## REMOVED Requirements

### Requirement: Prune requests specify exactly one pruning strategy
**Reason**: Manual inventory-scoped prune violates schedule independence (`SPEC.md` §11) and history immutability (`SPEC.md` §13-14). It is replaced by automated schedule-scoped retention.
**Migration**: Configure `retention` on recurring full schedules via `POST /backup/schedules/cluster/{cluster_id}`.
