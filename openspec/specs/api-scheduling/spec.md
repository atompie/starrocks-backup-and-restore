# api-scheduling Specification

## Purpose

Lets operators define recurring and one-shot backup schedules per cluster/group through the API instead of maintaining external cron entries by hand, and lets a lightweight periodic trigger (cron, Kubernetes CronJob) ask the server to run whatever is due. Schedules are the API path for creating backup jobs: one-shot schedules start full backups immediately, while incremental backups are available only through recurring schedules.

## Requirements

### Requirement: Define a recurring backup schedule
The system SHALL allow an authenticated client to create a recurring schedule — one that
specifies a `cadence` — against a registered cluster identified by a cluster id in the request
path, specifying job type (full or incremental backup), an inventory group id, a repository to
back up into, a cadence (cron expression or equivalent interval), an optional execution backend
override, and — when the job type is full backup — a `retention` count of at least 1 (the number
of successful full backups this schedule keeps). The system SHALL reject creation with HTTP 404 if
the inventory group id does not exist on that cluster, SHALL reject creation with HTTP 404 if the
named repository does not exist on that cluster (via the same live StarRocks-side catalog lookup
used to validate a direct backup job's repository), SHALL reject creation with HTTP 422 if a
full-backup recurring schedule omits `retention`, SHALL reject creation with HTTP 422 if an
incremental-backup schedule specifies `retention` (incremental backups are never subject to
retention), and, once accepted, SHALL persist it in the metadata store, computing its next-run
time from the cadence.

#### Scenario: Successful schedule creation
- **WHEN** an authenticated client creates a recurring full-backup schedule under a valid cluster
  id, with a valid inventory group id that exists on that cluster, a repository that exists on
  that cluster, a cadence, and a `retention` of at least 1
- **THEN** the system persists the schedule against that cluster, computes its next-run time, and
  returns the created schedule including the inventory group id, repository, and retention

#### Scenario: Schedule against an unknown cluster
- **WHEN** an authenticated client creates a schedule under a cluster id that is not registered
- **THEN** the system rejects the request with HTTP 404 and does not create a schedule

#### Scenario: Schedule against an unknown inventory group
- **WHEN** an authenticated client creates a schedule with an inventory group id that does not
  exist on the target cluster
- **THEN** the system rejects the request with HTTP 404 and does not create a schedule

#### Scenario: Schedule against an unknown repository
- **WHEN** an authenticated client creates a schedule naming a repository that does not exist on
  the target cluster
- **THEN** the system rejects the request with HTTP 404 and does not create a schedule

#### Scenario: Invalid cadence expression
- **WHEN** an authenticated client creates a recurring schedule with a cadence expression that
  cannot be parsed
- **THEN** the system rejects the request with a 422 validation error

#### Scenario: Recurring full-backup schedule missing retention
- **WHEN** an authenticated client creates a recurring full-backup schedule without a `retention`
  value
- **THEN** the system rejects the request with HTTP 422 and does not create a schedule

#### Scenario: Recurring incremental-backup schedule specifying retention
- **WHEN** an authenticated client creates a recurring incremental-backup schedule with a
  `retention` value set
- **THEN** the system rejects the request with HTTP 422 and does not create a schedule

### Requirement: Create a one-shot schedule
The system SHALL allow an authenticated client to create a one-shot schedule by omitting
`cadence` (or supplying it as null) against a registered cluster, specifying job type, inventory
group id, repository, an optional execution backend override, and an optional `expire_after_days`
(a positive integer; omitted or null means the schedule never expires). The system SHALL reject
creation with HTTP 422 if the job type is `backup_incremental` — a one-shot schedule is a full
backup only; incremental backups come only from recurring schedules. The system SHALL reject
creation with HTTP 422 if a one-shot schedule specifies `retention` — count-based retention does
not apply to a schedule that only ever produces one job. On successful creation the system SHALL
persist the schedule with `cadence` and `next_run_at` both null, and SHALL immediately submit
exactly one job for it through the same job-submission path used by a direct API job submission,
recording that job's id against the schedule the same way a recurring schedule's triggered job is
recorded.

#### Scenario: Successful one-shot schedule creation submits exactly one job
- **WHEN** an authenticated client creates a one-shot full-backup schedule under a valid cluster
  id, with a valid inventory group id, a valid repository, and no `cadence`
- **THEN** the system persists the schedule with `cadence` and `next_run_at` null, immediately
  submits exactly one job for it, and returns the created schedule with that job recorded as its
  most recent run

#### Scenario: One-shot incremental schedule is rejected
- **WHEN** an authenticated client attempts to create a one-shot schedule with `job_type` set to
  `backup_incremental`
- **THEN** the system rejects the request with HTTP 422 and does not create a schedule or submit
  a job

#### Scenario: One-shot schedule specifying retention is rejected
- **WHEN** an authenticated client attempts to create a one-shot schedule with a `retention` value
  set
- **THEN** the system rejects the request with HTTP 422 and does not create a schedule

#### Scenario: One-shot schedule with no expiry never expires
- **WHEN** an authenticated client creates a one-shot schedule without specifying
  `expire_after_days`
- **THEN** the system persists the schedule with `expire_after_days` null, meaning it is never
  automatically removed by expiry

#### Scenario: One-shot schedule with an expiry is recorded verbatim
- **WHEN** an authenticated client creates a one-shot schedule with `expire_after_days` set to a
  positive integer
- **THEN** the system persists that value on the schedule and returns it in the created schedule

### Requirement: List, update, and remove schedules
The system SHALL allow an authenticated client to list the schedules registered against a
specific cluster (identified by a cluster id in the request path), update a recurring schedule's
cadence/inventory group id/repository/backend/enabled state/retention, and delete a schedule
(recurring or one-shot), with update and delete also scoped to that cluster. Updating a schedule's
`repository` SHALL be rejected with HTTP 404 if the named repository does not exist on that
cluster. Update SHALL reject with HTTP 409 any request targeting an existing one-shot schedule
(one whose `cadence` is null), regardless of which field the request targets, since a one-shot
schedule is immutable once created. Update SHALL reject with HTTP 422 a request that would set a
recurring schedule's `cadence` to null (converting a schedule's shot-type after creation is not
supported). Update SHALL reject with HTTP 422 a request that would leave `retention` inconsistent
with the schedule's resulting `job_type` under the creation-time rules (a request changing
`job_type` to `backup_incremental` while `retention` remains set on the schedule must clear
`retention` in the same request or be rejected). Deleting a one-shot schedule removes it the same
way deleting a recurring schedule does.

#### Scenario: Listing a cluster's schedules
- **WHEN** an authenticated client lists schedules under a valid cluster id
- **THEN** the system returns every schedule registered against that cluster, recurring and
  one-shot alike, each including its inventory group id and repository

#### Scenario: Listing schedules for an unknown cluster
- **WHEN** an authenticated client lists schedules under a cluster id that is not registered
- **THEN** the system rejects the request with HTTP 404

#### Scenario: Disabling a schedule stops it from running
- **WHEN** an authenticated client updates a recurring schedule's enabled flag to false under that
  schedule's own cluster id
- **THEN** the system excludes that schedule from future due-schedule runs until it is re-enabled

#### Scenario: Deleting a schedule
- **WHEN** an authenticated client deletes an existing schedule (recurring or one-shot) under that
  schedule's own cluster id
- **THEN** the system removes it from the registry and it is no longer considered for due-schedule
  runs

#### Scenario: Accessing a schedule through the wrong cluster
- **WHEN** an authenticated client gets, updates, or deletes a schedule id using a cluster id that
  the schedule does not belong to
- **THEN** the system responds with HTTP 404, the same as if the schedule did not exist

#### Scenario: Updating a schedule to an unknown repository
- **WHEN** an authenticated client updates an existing recurring schedule's `repository` to a name
  that does not exist on that schedule's cluster
- **THEN** the system rejects the request with HTTP 404 and does not modify the stored schedule

#### Scenario: Updating a one-shot schedule is rejected
- **WHEN** an authenticated client attempts to update any field of an existing one-shot schedule
- **THEN** the system rejects the request with HTTP 409 and does not modify the stored schedule

#### Scenario: Converting a recurring schedule to one-shot is rejected
- **WHEN** an authenticated client updates an existing recurring schedule's `cadence` to null
- **THEN** the system rejects the request with HTTP 422 and does not modify the stored schedule

#### Scenario: Updating job type to incremental while retention remains set is rejected
- **WHEN** an authenticated client updates a recurring full-backup schedule's `job_type` to
  `backup_incremental` without also clearing its existing `retention` value in the same request
- **THEN** the system rejects the request with HTTP 422 and does not modify the stored schedule

### Requirement: Running due schedules submits jobs through the standard job system
The system SHALL expose an endpoint that, when called, finds all enabled recurring schedules
whose next-run time has passed, submits a job for each through the same job-submission path used
by direct API job submission (including backend selection, the schedule's inventory group id, and
the schedule's repository), records that schedule as the job's origin, and advances each triggered
schedule's next-run time. The system SHALL exclude one-shot schedules (`cadence` null) from
due-schedule evaluation entirely: a one-shot schedule's only job is submitted once, at creation,
and it has no recurring next-run time to evaluate.

#### Scenario: Due schedule is triggered
- **WHEN** the run-due endpoint is called and a recurring schedule's next-run time has passed
- **THEN** the system submits a job for that schedule's cluster/job-type/inventory group
  id/repository, records that schedule as the job's origin, and updates the schedule's next-run
  time to the next occurrence after now

#### Scenario: Not-yet-due schedule is skipped
- **WHEN** the run-due endpoint is called and a recurring schedule's next-run time has not yet
  passed
- **THEN** the system does not submit a job for that schedule and leaves its next-run time
  unchanged

#### Scenario: No schedules are due
- **WHEN** the run-due endpoint is called and no enabled recurring schedule is due
- **THEN** the system responds successfully indicating zero jobs were triggered

#### Scenario: Run-due call is idempotent per due occurrence
- **WHEN** the run-due endpoint is called twice in quick succession before a triggered job's
  schedule advances its next-run time
- **THEN** the second call does not submit a duplicate job for a schedule already triggered for
  its current due occurrence

#### Scenario: One-shot schedule is never selected by run-due
- **WHEN** the run-due endpoint is called and a one-shot schedule exists (regardless of when it
  was created)
- **THEN** the system does not submit a job for it and does not modify it

### Requirement: Schedule deletion performs validated asynchronous cleanup
The system SHALL synchronously validate a schedule deletion request before accepting it. It SHALL
respond with HTTP 409 and create no cleanup job if any backup job submitted by the schedule is
PENDING or RUNNING, if any PENDING or RUNNING restore uses one of its backup jobs, or if a full
backup produced by the schedule is the baseline of an existing incremental backup from another
schedule. When accepted, the system SHALL mark the schedule as pending
deletion so no later scheduler tick submits work for it, create a `schedule_cleanup` job, and
respond with HTTP 202 containing that job's id and status. Cleanup SHALL drop each distinct
`(repository, snapshot_label)` referenced by the schedule's backup jobs, then delete those jobs,
their histories and references, restore jobs that use them as their source, and finally the
schedule. A snapshot already absent from its repository SHALL count as dropped so a failed cleanup
can be retried. Repeating DELETE while cleanup is PENDING or RUNNING SHALL return HTTP 202 with
the existing cleanup job; after a cleanup job fails, a later DELETE MAY submit a new cleanup job.

#### Scenario: Schedule deletion is accepted asynchronously
- **WHEN** an authenticated client deletes a schedule with no active jobs or dependent incremental
  baseline
- **THEN** the system responds with HTTP 202 and the id and status of a `schedule_cleanup` job;
  the schedule remains pending deletion until cleanup completes

#### Scenario: Deletion is blocked by a pending or running job
- **WHEN** an authenticated client deletes a schedule that has a PENDING or RUNNING backup job
- **THEN** the system responds with HTTP 409, creates no cleanup job, and leaves the schedule
  unchanged

#### Scenario: Deletion is blocked by an incremental baseline dependency
- **WHEN** an authenticated client deletes a schedule whose full backup is the baseline of an
  existing incremental backup from another schedule
- **THEN** the system responds with HTTP 409, creates no cleanup job, and leaves the schedule
  unchanged

#### Scenario: Deletion is blocked by a running dependent restore
- **WHEN** an authenticated client deletes a schedule while a PENDING or RUNNING restore uses one
  of its backup jobs
- **THEN** the system responds with HTTP 409, creates no cleanup job, and leaves the schedule
  unchanged

#### Scenario: Accepted deletion prevents another due backup
- **WHEN** a recurring schedule has been accepted for deletion and a scheduler tick runs before
  cleanup finishes
- **THEN** the tick does not submit another backup job for that schedule

#### Scenario: Repeated deletion returns the existing cleanup job
- **WHEN** an authenticated client deletes a schedule whose cleanup job is already PENDING or
  RUNNING
- **THEN** the system responds with HTTP 202 and the id of that existing cleanup job without
  creating a second cleanup job

#### Scenario: Cleanup drops each snapshot once and removes dependent records
- **WHEN** a schedule has multiple reference rows for the same snapshot, or a snapshot was already
  dropped by an earlier partial cleanup attempt
- **THEN** cleanup drops each distinct repository/snapshot pair at most once and, after all drops
  succeed, removes the schedule's backup jobs, their histories and references, dependent restore
  jobs and histories, and the schedule

#### Scenario: Failed cleanup can be retried
- **WHEN** a `schedule_cleanup` job fails while the schedule still exists
- **THEN** the system leaves the schedule pending deletion and a later DELETE request may submit a
  new cleanup job that safely continues cleanup

### Requirement: Expired one-shot schedules use schedule cleanup
The scheduler tick SHALL find one-shot schedules with `expire_after_days` set whose
`created_at + expire_after_days` is at or before the current time and submit the same guarded
`schedule_cleanup` operation used by operator deletion. Expiry SHALL NOT affect one-shot schedules
whose `expire_after_days` is null. If deletion is blocked by an active job or a dependent
incremental baseline, the tick SHALL leave the schedule in place, log the reason, and retry on a
later tick. If an expiry cleanup job fails, a later tick SHALL retry cleanup while the schedule
remains pending deletion.

#### Scenario: Expired one-shot schedule is cleaned up
- **WHEN** a scheduler tick runs after a one-shot schedule's configured expiry time and deletion
  validation passes
- **THEN** the system submits a `schedule_cleanup` job for that schedule

#### Scenario: Blocked expiry is retried on a later tick
- **WHEN** an expired one-shot schedule is blocked from deletion by a pending or running job or a
  dependent incremental baseline
- **THEN** the tick logs the reason and does not remove the schedule, and a later tick checks it
  again

#### Scenario: Never-expiring one-shot schedule is kept
- **WHEN** a scheduler tick runs for a one-shot schedule whose `expire_after_days` is null
- **THEN** the system does not submit a cleanup job for that schedule
