## MODIFIED Requirements

### Requirement: CLI can submit and monitor jobs via the API
The system SHALL provide CLI commands to submit a backup/restore/prune job against a registered cluster and to check a job's status/progress, by calling the corresponding API endpoints, and SHALL support waiting (polling) until the job reaches a terminal state when requested. Where a job-submission command accepts an inventory group, the CLI SHALL accept the group by its human-readable name, SHALL resolve that name to the group's id scoped to the target cluster before calling the API (which accepts only an id), and SHALL fail the invocation with a clear error before submitting a job if the name does not resolve to an existing group on that cluster.

#### Scenario: Submit and wait
- **WHEN** a user runs the job-submission CLI command with a wait flag
- **THEN** the CLI submits the job via the API, polls its status until it reaches SUCCESS or FAILED, and exits with a status code reflecting the outcome

#### Scenario: Submitting a backup job with a group name
- **WHEN** a user runs the backup-submission CLI command with `--group <name>` naming an inventory group that exists on the target cluster
- **THEN** the CLI resolves the name to that group's id and submits the job to the API using the resolved id

#### Scenario: Submitting a job with an unresolvable group name
- **WHEN** a user runs a job-submission CLI command with `--group <name>` naming a group that does not exist on the target cluster
- **THEN** the CLI exits with a non-zero status and a clear error, and does not submit a job

### Requirement: CLI can manage schedules against a specific cluster
The system SHALL provide CLI commands to create a schedule against a specific cluster, list the schedules registered against a specific cluster, and remove a schedule, by calling the corresponding cluster-scoped API endpoints. The schedule-add command SHALL accept the inventory group by its human-readable name, SHALL resolve that name to the group's id scoped to the target cluster before calling the API (which accepts only an inventory group id), and SHALL fail the invocation with a clear error before creating a schedule if the name does not resolve.

#### Scenario: Add a schedule for a cluster via CLI
- **WHEN** a user runs the schedule-add CLI command with a cluster id, job type, a group name that exists on that cluster, and cadence
- **THEN** the CLI resolves the group name to its id, calls the API to create the schedule under that cluster with the resolved id, and prints the created schedule's id and next run time

#### Scenario: Add a schedule with an unresolvable group name
- **WHEN** a user runs the schedule-add CLI command with a group name that does not exist on the target cluster
- **THEN** the CLI exits with a non-zero status and a clear error, and does not create a schedule

#### Scenario: List a cluster's schedules via CLI
- **WHEN** a user runs the schedule-list CLI command with a cluster id
- **THEN** the CLI calls the API to list only the schedules registered against that cluster
