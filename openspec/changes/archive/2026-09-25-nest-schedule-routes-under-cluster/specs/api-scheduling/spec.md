## MODIFIED Requirements

### Requirement: Define a recurring backup schedule
The system SHALL allow an authenticated client to create a schedule against a registered cluster identified by a cluster id in the request path, specifying job type (full or incremental backup), inventory group, a cadence (cron expression or equivalent interval), and an optional execution backend override, and SHALL persist it in the metadata store.

#### Scenario: Successful schedule creation
- **WHEN** an authenticated client creates a schedule under a valid cluster id, with a valid job type, group, and cadence
- **THEN** the system persists the schedule against that cluster, computes its next-run time, and returns the created schedule

#### Scenario: Schedule against an unknown cluster
- **WHEN** an authenticated client creates a schedule under a cluster id that is not registered
- **THEN** the system rejects the request with HTTP 404 and does not create a schedule

#### Scenario: Invalid cadence expression
- **WHEN** an authenticated client creates a schedule with a cadence expression that cannot be parsed
- **THEN** the system rejects the request with a 422 validation error

### Requirement: List, update, and remove schedules
The system SHALL allow an authenticated client to list the schedules registered against a specific cluster (identified by a cluster id in the request path), update a schedule's cadence/group/backend/enabled state, and delete a schedule, with update and delete also scoped to that cluster.

#### Scenario: Listing a cluster's schedules
- **WHEN** an authenticated client lists schedules under a valid cluster id
- **THEN** the system returns only the schedules registered against that cluster

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
