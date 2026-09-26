## MODIFIED Requirements

### Requirement: Define a recurring backup schedule
The system SHALL allow an authenticated client to create a schedule against a registered cluster identified by a cluster id in the request path, specifying job type (full or incremental backup), an inventory group id, a repository to back up into, a cadence (cron expression or equivalent interval), and an optional execution backend override, SHALL reject creation with HTTP 404 if the inventory group id does not exist on that cluster, SHALL reject creation with HTTP 404 if the named repository does not exist on that cluster (via the same live StarRocks-side catalog lookup used to validate a direct backup job's repository), and SHALL persist it in the metadata store.

#### Scenario: Successful schedule creation
- **WHEN** an authenticated client creates a schedule under a valid cluster id, with a valid job type, an inventory group id that exists on that cluster, a repository that exists on that cluster, and cadence
- **THEN** the system persists the schedule against that cluster, computes its next-run time, and returns the created schedule including the inventory group id and repository

#### Scenario: Schedule against an unknown cluster
- **WHEN** an authenticated client creates a schedule under a cluster id that is not registered
- **THEN** the system rejects the request with HTTP 404 and does not create a schedule

#### Scenario: Schedule against an unknown inventory group
- **WHEN** an authenticated client creates a schedule with an inventory group id that does not exist on the target cluster
- **THEN** the system rejects the request with HTTP 404 and does not create a schedule

#### Scenario: Schedule against an unknown repository
- **WHEN** an authenticated client creates a schedule naming a repository that does not exist on the target cluster
- **THEN** the system rejects the request with HTTP 404 and does not create a schedule

#### Scenario: Invalid cadence expression
- **WHEN** an authenticated client creates a schedule with a cadence expression that cannot be parsed
- **THEN** the system rejects the request with a 422 validation error

### Requirement: List, update, and remove schedules
The system SHALL allow an authenticated client to list the schedules registered against a specific cluster (identified by a cluster id in the request path), update a schedule's cadence/inventory group id/repository/backend/enabled state, and delete a schedule, with update and delete also scoped to that cluster. Updating a schedule's `repository` SHALL be rejected with HTTP 404 if the named repository does not exist on that cluster.

#### Scenario: Listing a cluster's schedules
- **WHEN** an authenticated client lists schedules under a valid cluster id
- **THEN** the system returns only the schedules registered against that cluster, each including its inventory group id and repository

#### Scenario: Listing schedules for an unknown cluster
- **WHEN** an authenticated client lists schedules under a cluster id that is not registered
- **THEN** the system rejects the request with HTTP 404

#### Scenario: Disabling a schedule stops it from running
- **WHEN** an authenticated client updates a schedule's enabled flag to false under that schedule's own cluster id
- **THEN** the system excludes that schedule from future due-schedule runs until it is re-enabled

#### Scenario: Deleting a schedule
- **WHEN** an authenticated client deletes an existing schedule under that schedule's own cluster id
- **THEN** the system removes it from the registry and it is no longer considered for due-schedule runs

#### Scenario: Accessing a schedule through the wrong cluster
- **WHEN** an authenticated client gets, updates, or deletes a schedule id using a cluster id that the schedule does not belong to
- **THEN** the system responds with HTTP 404, the same as if the schedule did not exist

#### Scenario: Updating a schedule to an unknown repository
- **WHEN** an authenticated client updates an existing schedule's `repository` to a name that does not exist on that schedule's cluster
- **THEN** the system rejects the request with HTTP 404 and does not modify the stored schedule

### Requirement: Running due schedules submits jobs through the standard job system
The system SHALL expose an endpoint that, when called, finds all enabled schedules whose next-run time has passed, submits a job for each through the same job-submission path used by direct API job submission (including backend selection, the schedule's inventory group id, and the schedule's repository), and advances each triggered schedule's next-run time.

#### Scenario: Due schedule is triggered
- **WHEN** the run-due endpoint is called and a schedule's next-run time has passed
- **THEN** the system submits a job for that schedule's cluster/job-type/inventory group id/repository, and updates the schedule's next-run time to the next occurrence after now

#### Scenario: Not-yet-due schedule is skipped
- **WHEN** the run-due endpoint is called and a schedule's next-run time has not yet passed
- **THEN** the system does not submit a job for that schedule and leaves its next-run time unchanged

#### Scenario: No schedules are due
- **WHEN** the run-due endpoint is called and no enabled schedule is due
- **THEN** the system responds successfully indicating zero jobs were triggered

#### Scenario: Run-due call is idempotent per due occurrence
- **WHEN** the run-due endpoint is called twice in quick succession before a triggered job's schedule advances its next-run time
- **THEN** the second call does not submit a duplicate job for a schedule already triggered for its current due occurrence
