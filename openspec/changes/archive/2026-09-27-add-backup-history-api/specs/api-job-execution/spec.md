## ADDED Requirements

### Requirement: Backup job history can be listed for a cluster
The system SHALL expose an endpoint to list jobs for a registered cluster, ordered most recently
created first, each with its current status (PENDING, RUNNING, SUCCESS, FAILED), timestamps
(created, started, finished), and, when the underlying StarRocks operation reports a numeric
progress indicator, a progress value reflecting the most recently observed value. By default the
listing SHALL be scoped to backup jobs (`backup_full` and `backup_incremental`); the endpoint
SHALL accept an optional job type filter to include a different job type instead, and an optional
status filter to narrow the listing to jobs currently in a given status. The endpoint SHALL accept
`limit` and `offset` parameters to page through results, and SHALL respond with HTTP 404 without
returning any listing if the cluster id is not registered.

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
