## ADDED Requirements

### Requirement: A job's backup references can be retrieved
The system SHALL expose an endpoint to retrieve the backup references recorded for a job, returned
as a list, each entry carrying the repository, snapshot label, snapshot timestamp, database,
table, and (when applicable) partition it covers. References are recorded only once the job's
underlying StarRocks backup operation reaches `FINISHED`; a job that has not yet finished, or that
finished as `FAILED`, SHALL have no references. The endpoint SHALL respond with HTTP 404 if the job
id does not exist.

#### Scenario: Retrieving references for a successful backup job
- **WHEN** an authenticated client requests the references of a backup job that finished with
  status `SUCCESS`
- **THEN** the system responds with HTTP 200 and a list of the references recorded for that job

#### Scenario: Retrieving references for a failed backup job
- **WHEN** an authenticated client requests the references of a backup job that finished with
  status `FAILED`
- **THEN** the system responds with HTTP 200 and an empty list

#### Scenario: Retrieving references for a job still running
- **WHEN** an authenticated client requests the references of a backup job that is still `RUNNING`
- **THEN** the system responds with HTTP 200 and an empty list, since references are only recorded
  once the job's StarRocks operation reaches `FINISHED`

#### Scenario: Retrieving references for an unknown job
- **WHEN** an authenticated client requests the references of a job id that does not exist
- **THEN** the system responds with HTTP 404

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
SHALL respond with HTTP 404 without creating a job if it does not. A full or incremental backup
request SHALL also include a `repository` naming the destination backup repository; the system
SHALL reject a request missing `repository` with HTTP 422 before any job is created, and SHALL
synchronously verify that a repository with that name currently exists on the target cluster
(via the same live StarRocks-side catalog lookup used to list repositories) before creating the
job, responding with HTTP 404 without creating a job if it does not. An inventory group targeted by
a full or incremental backup request MAY span more than one database; the system SHALL execute one
StarRocks backup operation per database in the group under the single created Backup Job, and SHALL
record backup references for every database/table the job covers once each database's operation
reaches `FINISHED`.

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

#### Scenario: Full backup submitted for a group spanning multiple databases
- **WHEN** an authenticated client submits a full backup request naming an inventory group whose
  table memberships span two databases
- **THEN** the system responds with HTTP 202 and a single job id, and the job's execution backs up
  both databases, recording backup references for each once its StarRocks operation finishes
