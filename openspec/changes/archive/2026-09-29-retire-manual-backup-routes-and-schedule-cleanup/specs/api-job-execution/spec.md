## MODIFIED Requirements

### Requirement: Submitting an operation returns immediately with a job
The system SHALL accept requests to start a restore or prune operation against a registered
cluster, SHALL create a job record in PENDING state, SHALL return an HTTP 202 response containing
the job id and status without waiting for the operation to complete, and SHALL begin executing the
operation asynchronously. Backup Jobs created by schedule submission SHALL use the same
asynchronous job lifecycle and SHALL record references for every database/table covered once each
database's StarRocks operation reaches `FINISHED`. The system SHALL NOT expose manual full or
incremental backup submission routes.

#### Scenario: Manual backup submission routes are retired
- **WHEN** an authenticated client submits a request to `/backup/manual/full/cluster/{cluster_id}`
  or `/backup/manual/incremental/cluster/{cluster_id}`
- **THEN** the system responds with HTTP 404 and creates no job; clients create a one-shot schedule
  for an immediate full backup or a recurring schedule for an incremental backup

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

## ADDED Requirements

### Requirement: Restore submission cannot race with source backup cleanup
When a restore request identifies a source backup whose schedule is pending deletion, the system
SHALL reject the request with HTTP 409 before creating a Restore Job. Resolving the source backup
and creating the Restore Job SHALL be coordinated with schedule deletion so either the restore job
is visible to deletion validation, or the pending-deletion state is visible to restore submission.

#### Scenario: Restore submission is blocked after cleanup is accepted
- **WHEN** an authenticated client requests a restore from a backup whose schedule is pending
  deletion
- **THEN** the system responds with HTTP 409 and creates no Restore Job
