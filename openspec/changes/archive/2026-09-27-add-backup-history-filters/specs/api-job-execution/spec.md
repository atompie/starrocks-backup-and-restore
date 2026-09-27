## MODIFIED Requirements

### Requirement: Backup job history can be listed for a cluster
The system SHALL expose an endpoint to list jobs for a registered cluster, ordered most recently
created first, each with its current status (PENDING, RUNNING, SUCCESS, FAILED), timestamps
(created, started, finished), and, when the underlying StarRocks operation reports a numeric
progress indicator, a progress value reflecting the most recently observed value. By default the
listing SHALL be scoped to backup jobs (`backup_full` and `backup_incremental`); the endpoint
SHALL accept an optional job type filter to include a different job type instead, and an optional
status filter to narrow the listing to jobs currently in a given status. The endpoint SHALL accept
an optional `job_id` filter to narrow the listing to the single job with that id, and an optional
`group_id` filter to narrow the listing to jobs submitted for that inventory group; a `group_id`
filter SHALL only match job types that carry a group id, so jobs of other types are excluded when
it is applied. The endpoint SHALL accept `limit` and `offset` parameters to page through results,
and SHALL respond with HTTP 404 without returning any listing if the cluster id is not registered.

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
