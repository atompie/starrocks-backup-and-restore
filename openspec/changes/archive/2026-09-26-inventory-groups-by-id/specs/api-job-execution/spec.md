## MODIFIED Requirements

### Requirement: Submitting an operation returns immediately with a job
The system SHALL accept requests to start a full backup, incremental backup, restore, or prune
operation against a registered cluster, SHALL create a job record in PENDING state, SHALL return
an HTTP 202 response containing the job id and status without waiting for the operation to
complete, and SHALL begin executing the operation asynchronously. For a full or incremental backup
request, the system SHALL reject a request that is missing `group_id` (or supplies a value that
cannot be parsed as an inventory group id) with HTTP 422 before any job is created. For a full or
incremental backup request that does supply a `group_id`, the system SHALL synchronously verify
that an inventory group with that id exists on the target cluster before creating the job, and
SHALL respond with HTTP 404 without creating a job if it does not.

#### Scenario: Full backup submission
- **WHEN** an authenticated client submits a full backup request for a registered cluster and an
  inventory group id that exists on that cluster
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

### Requirement: Restore requests accept at most one of group or table
The system SHALL reject a restore request that specifies both `group_id` and `table` with HTTP 422
before any job is created. A restore request specifying neither SHALL be accepted and SHALL
restore every table present in the target backup.

#### Scenario: Both group and table specified
- **WHEN** an authenticated client submits a restore request specifying both `group_id` and
  `table`
- **THEN** the system responds with HTTP 422 and does not create a job

#### Scenario: Neither group nor table specified
- **WHEN** an authenticated client submits a restore request specifying neither `group_id` nor
  `table`
- **THEN** the system responds with HTTP 202 and creates a job that restores every table present
  in the target backup
