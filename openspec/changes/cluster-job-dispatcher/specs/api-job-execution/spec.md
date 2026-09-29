## MODIFIED Requirements

### Requirement: Submitting an operation returns immediately with a job
The system SHALL accept requests to start a restore or prune operation against a registered
cluster, SHALL create a job record in PENDING state, SHALL return
an HTTP 202 response containing the job id and status without waiting for the operation to
complete, and SHALL leave the job PENDING until a scheduler tick starts it. Backup Jobs created by
schedule submission SHALL use the same queued lifecycle and SHALL record references for every
database/table covered once each database's StarRocks operation reaches `FINISHED`. The system
SHALL NOT expose manual full or incremental backup submission routes.

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

#### Scenario: A submitted job waits for a scheduler tick
- **WHEN** an authenticated client submits a restore and no scheduler tick has run since
- **THEN** the response is HTTP 202 with status `PENDING` and the job stays `PENDING` until a tick admits it

## ADDED Requirements

### Requirement: Jobs on one cluster run one at a time, in priority order
The system SHALL start queued jobs only from the scheduler tick's dispatch step. For each cluster
it SHALL admit at most one job per tick, and only when no job is `RUNNING` on that cluster,
regardless of job type. Among that cluster's `PENDING` jobs it SHALL admit restore jobs before
backup jobs (`backup_full`, `backup_incremental`) and backup jobs before every other job type,
oldest first within a priority. Admission SHALL transition the job from `PENDING` to `RUNNING` (setting
its start time and heartbeat) before the job is handed to its execution backend. Jobs on different clusters
SHALL be admitted independently and MAY run concurrently. A job that has to wait for its cluster SHALL
remain `PENDING` and SHALL NOT fail because the cluster is busy.

#### Scenario: Two jobs on one cluster run one after another
- **WHEN** two `PENDING` jobs exist for the same cluster and none is `RUNNING`
- **THEN** a tick admits only the higher-priority (or older) one, the other stays `PENDING`, and it is
  admitted on a later tick after the first is no longer `RUNNING`

#### Scenario: Jobs on different clusters run in parallel
- **WHEN** each of two clusters has a `PENDING` job and neither cluster has a `RUNNING` job
- **THEN** a single tick admits one job on each cluster

#### Scenario: Restore is admitted before backup
- **WHEN** a cluster has a `PENDING` backup and a `PENDING` restore and no `RUNNING` job
- **THEN** the restore is admitted first

#### Scenario: A busy cluster admits nothing
- **WHEN** a cluster has a `RUNNING` job and `PENDING` jobs
- **THEN** the tick admits none of them and leaves them `PENDING` without failing them

#### Scenario: Overlapping ticks do not double-admit
- **WHEN** two dispatch passes consider the same `PENDING` job at the same time
- **THEN** exactly one conditional update succeeds, only that pass enqueues the job, and the cluster
  ends with one `RUNNING` job
