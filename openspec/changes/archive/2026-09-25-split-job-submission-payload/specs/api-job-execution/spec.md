## MODIFIED Requirements

### Requirement: Submitting an operation returns immediately with a job
The system SHALL accept requests to start a full backup, incremental backup, restore, or prune
operation against a registered cluster, SHALL create a job record in PENDING state, SHALL return
an HTTP 202 response containing the job id and status without waiting for the operation to
complete, and SHALL begin executing the operation asynchronously. For a full or incremental backup
request, the system SHALL reject a request that is missing `group` (or supplies an empty value)
with HTTP 422 before any job is created. For a full or incremental backup request that does supply
a `group`, the system SHALL synchronously verify that the group exists on the target cluster before
creating the job, and SHALL respond with HTTP 404 without creating a job if it does not.

#### Scenario: Full backup submission
- **WHEN** an authenticated client submits a full backup request for a registered cluster and an
  inventory group that exists on that cluster
- **THEN** the system responds with HTTP 202, a job id, and status PENDING, and the operation
  continues running after the response is sent

#### Scenario: Submission against an unknown cluster
- **WHEN** an authenticated client submits any job against a cluster id that is not registered
- **THEN** the system responds with HTTP 404 and does not create a job

#### Scenario: Full or incremental backup submitted with an unknown group
- **WHEN** an authenticated client submits a full or incremental backup request naming an
  inventory group that does not exist on the target cluster
- **THEN** the system responds with HTTP 404, does not create a job, and no asynchronous failure
  is produced

#### Scenario: Full or incremental backup submitted with no group
- **WHEN** an authenticated client submits a full or incremental backup request with no inventory
  group specified, or an empty one
- **THEN** the system responds with HTTP 422 before any job is created, rather than accepting the
  request and failing asynchronously

## ADDED Requirements

### Requirement: Each job-submission endpoint validates a request schema scoped to its own fields
The system SHALL reject a job-submission request that includes a field not used by that specific
endpoint with HTTP 422, rather than silently accepting and ignoring it.

#### Scenario: Foreign field rejected
- **WHEN** an authenticated client submits a full backup request that includes a field only used
  by another job type (e.g. `keep_last`, which only prune uses)
- **THEN** the system responds with HTTP 422 and does not create a job

### Requirement: Restore requests accept at most one of group or table
The system SHALL reject a restore request that specifies both `group` and `table` with HTTP 422
before any job is created. A restore request specifying neither SHALL be accepted and SHALL
restore every table present in the target backup.

#### Scenario: Both group and table specified
- **WHEN** an authenticated client submits a restore request specifying both `group` and `table`
- **THEN** the system responds with HTTP 422 and does not create a job

#### Scenario: Neither group nor table specified
- **WHEN** an authenticated client submits a restore request specifying neither `group` nor
  `table`
- **THEN** the system responds with HTTP 202 and creates a job that restores every table present
  in the target backup

### Requirement: Prune requests specify exactly one pruning strategy
The system SHALL reject a prune request that specifies zero, or more than one, of `keep_last`,
`older_than`, `snapshot`, `snapshots` with HTTP 422 before any job is created.

#### Scenario: No strategy specified
- **WHEN** an authenticated client submits a prune request specifying none of `keep_last`,
  `older_than`, `snapshot`, `snapshots`
- **THEN** the system responds with HTTP 422 and does not create a job

#### Scenario: Multiple strategies specified
- **WHEN** an authenticated client submits a prune request specifying more than one of
  `keep_last`, `older_than`, `snapshot`, `snapshots`
- **THEN** the system responds with HTTP 422 and does not create a job
