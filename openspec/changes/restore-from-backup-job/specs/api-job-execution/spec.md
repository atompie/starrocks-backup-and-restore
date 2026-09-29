## ADDED Requirements

### Requirement: Restore is requested from a Backup Job, optionally into another cluster
The system SHALL expose `POST /restore/manual/cluster/{cluster_id}` where `cluster_id` is the source
cluster. The request SHALL carry `source_job_id` and MAY carry `target_cluster_id`; when
`target_cluster_id` is omitted the target is the source cluster. Before creating any Restore Job the
system SHALL reject the request with HTTP 404 if the source cluster, the source job or the target cluster
does not exist, or if the source job does not belong to the source cluster; with HTTP 409 if the source job
is not a backup job, is not `SUCCESS`, has had its data deleted, or its Schedule is pending deletion. On
acceptance the system SHALL create a `PENDING` Restore Job that belongs to the source cluster, records the
source job and the target cluster, and SHALL respond with HTTP 202.

#### Scenario: Restore into the same cluster
- **WHEN** a client requests a restore of a successful backup job and omits `target_cluster_id`
- **THEN** the system responds with HTTP 202 and the Restore Job targets the source cluster

#### Scenario: Restore into another cluster
- **WHEN** a client requests a restore of a successful full backup job with a different, existing
  `target_cluster_id`
- **THEN** the system responds with HTTP 202 and the Restore Job records that target cluster

#### Scenario: Unknown source job or target cluster
- **WHEN** the `source_job_id` or `target_cluster_id` does not exist, or the source job belongs to a
  different cluster than the path cluster
- **THEN** the system responds with HTTP 404 and creates no job

#### Scenario: Source job is not restorable
- **WHEN** the source job is a failed job, a non-backup job, or a backup whose data was deleted
- **THEN** the system responds with HTTP 409 and creates no job

### Requirement: Cross-cluster restore uses full backups and an existing target repository
When the target cluster differs from the source cluster the system SHALL reject an incremental source
job with HTTP 422. Restore SHALL NOT create or modify repositories. At submission the system SHALL verify
against the live clusters that the target cluster has a repository at the same location as the repository
holding the source backup, and SHALL reject the request with HTTP 409, naming the source repository and its
location, when it does not. The restore SHALL use the target cluster's own repository name. When either
cluster cannot be reached for this check the system SHALL respond with HTTP 502 or 503 and create no job.
On execution the system SHALL create the backed-up databases on the target cluster when they do not exist.

#### Scenario: Incremental source into another cluster
- **WHEN** a client requests a restore of an incremental backup job into a different cluster
- **THEN** the system responds with HTTP 422 and creates no job

#### Scenario: Target has no repository at the source location
- **WHEN** a client requests a cross-cluster restore and the target cluster has no repository at the
  source repository's location
- **THEN** the system responds with HTTP 409 including the source repository's name and location and
  creates no job

#### Scenario: Target repository has a different name
- **WHEN** the target cluster has a repository at the same location under a different name
- **THEN** the request is accepted and the restore reads the snapshot through the target's repository

#### Scenario: Cluster unreachable during the check
- **WHEN** the source or target cluster cannot be reached while verifying the repository
- **THEN** the system responds with HTTP 502 or 503 and creates no job

#### Scenario: Database is created on the target
- **WHEN** a cross-cluster restore runs and the backed-up database does not exist on the target
- **THEN** the database is created and the tables are restored into it

### Requirement: Restore filters are resolved on the source cluster
The system SHALL resolve `group_id` against the source cluster and SHALL reject a group that does not
belong to the source cluster with HTTP 422. It SHALL select the tables to restore from the source job's
backup references: a `group_id` selects every referenced table matching the group's table memberships
(a whole-database membership matching every referenced table in that database), and `table` with
`database` selects that table, without querying the target cluster's tables.

#### Scenario: Group from the source cluster
- **WHEN** a cross-cluster restore names a `group_id` belonging to the source cluster
- **THEN** the Restore Job restores only the source job's referenced tables that match the group

#### Scenario: Group from another cluster
- **WHEN** the `group_id` belongs to a cluster other than the source cluster
- **THEN** the system responds with HTTP 422 and creates no job

### Requirement: Restore Jobs can be listed by cluster and by source backup
The system SHALL expose `GET /restore/history/cluster/{cluster_id}` listing Restore Jobs whose source
cluster is `cluster_id`, most recent first, with optional `source_job_id` and `status` filters and `limit`
/ `offset` paging, and `GET /backup/job/{job_id}/restores` listing the Restore Jobs that use that backup
job as their source. Each entry SHALL include the source job id and target cluster id. Both SHALL respond
with HTTP 404 for an unknown cluster or job.

#### Scenario: Restores of one backup job
- **WHEN** a client lists the restores of a backup job that was restored twice
- **THEN** the system responds with HTTP 200 and both Restore Jobs

#### Scenario: Restore history filtered by status
- **WHEN** a client lists restore history for a cluster with a `status` filter
- **THEN** only Restore Jobs in that status are returned

### Requirement: Restore does not modify its source backup
Executing a Restore Job SHALL NOT change the source Backup Job, its history, or its backup references.

#### Scenario: Source untouched after restore
- **WHEN** a restore of a backup job finishes, successfully or not
- **THEN** that backup job's status, history events and references are unchanged

### Requirement: Manual restore route is retired
The system SHALL NOT expose `/backup/manual/restore/cluster/{cluster_id}`.

#### Scenario: Old restore route
- **WHEN** a client submits a request to `/backup/manual/restore/cluster/{cluster_id}`
- **THEN** the system responds with HTTP 404 and creates no job

## MODIFIED Requirements

### Requirement: Jobs on one cluster run one at a time, in priority order
The system SHALL start queued jobs only from the scheduler tick's dispatch step. For each cluster
it SHALL admit at most one job per tick, and only when no job is `RUNNING` on that cluster,
regardless of job type. A Restore Job SHALL be considered in the lane of the cluster it executes on, which is its target
cluster (its source cluster when no target was given); every other job is considered in its own cluster's
lane. Among that lane's `PENDING` jobs it SHALL admit restore jobs before
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

#### Scenario: A cross-cluster restore occupies the target cluster
- **WHEN** a restore from cluster A targets cluster B and B has a `RUNNING` job
- **THEN** the restore stays `PENDING`, and A remains free to admit its own jobs

### Requirement: Restore requests accept at most one of group or table
The system SHALL reject a restore request that specifies both `group_id` and `table` with HTTP 422
before any job is created. A restore request specifying neither SHALL be accepted and SHALL
restore every table present in the source backup job. A restore request that specifies `table` SHALL
also specify `database` (identifying which database the bare table name belongs to), and the
system SHALL reject a `table` request missing `database` with HTTP 422 before any job is created;
a restore request that specifies `group_id` SHALL NOT require `database`, since the group's own
table memberships already identify each table's database. The system SHALL determine which
repository holds the backup from the source job's own recorded references rather than from any
client-supplied field.

#### Scenario: Both group and table specified
- **WHEN** an authenticated client submits a restore request specifying both `group_id` and
  `table`
- **THEN** the system responds with HTTP 422 and does not create a job

#### Scenario: Neither group nor table specified
- **WHEN** an authenticated client submits a restore request specifying neither `group_id` nor
  `table`
- **THEN** the system responds with HTTP 202 and creates a job that restores every table present
  in the source backup job

#### Scenario: Table specified without database
- **WHEN** an authenticated client submits a restore request specifying `table` but not
  `database`
- **THEN** the system responds with HTTP 422 and does not create a job

#### Scenario: Table specified with database
- **WHEN** an authenticated client submits a restore request specifying both `table` and
  `database`
- **THEN** the system responds with HTTP 202 and creates a job that restores that table from the
  specified database
