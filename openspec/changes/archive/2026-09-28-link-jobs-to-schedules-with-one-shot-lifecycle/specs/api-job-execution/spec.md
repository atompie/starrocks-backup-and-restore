# Spec Delta

## MODIFIED Requirements

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
- **WHEN** a client polls a job that is RUNNING and StarRocks currently reports a numeric progress
  percentage for that phase
- **THEN** the system returns status RUNNING together with the most recently observed progress
  percentage

#### Scenario: Progress is unavailable for a phase
- **WHEN** a client polls a job that is RUNNING during a phase where StarRocks reports no numeric
  progress
- **THEN** the system returns status RUNNING with started_at set and no progress percentage,
  rather than an error

#### Scenario: Completed job reports final state
- **WHEN** a client polls a job that has finished
- **THEN** the system returns status SUCCESS or FAILED, with started_at and finished_at both set,
  and an error message when FAILED

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
