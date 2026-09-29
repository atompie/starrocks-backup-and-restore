## ADDED Requirements

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
