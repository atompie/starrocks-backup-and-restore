## ADDED Requirements

### Requirement: CLI can manage schedules against a specific cluster
The system SHALL provide CLI commands to create a schedule against a specific cluster, list the schedules registered against a specific cluster, and remove a schedule, by calling the corresponding cluster-scoped API endpoints.

#### Scenario: Add a schedule for a cluster via CLI
- **WHEN** a user runs the schedule-add CLI command with a cluster id, job type, group, and cadence
- **THEN** the CLI calls the API to create the schedule under that cluster and prints the created schedule's id and next run time

#### Scenario: List a cluster's schedules via CLI
- **WHEN** a user runs the schedule-list CLI command with a cluster id
- **THEN** the CLI calls the API to list only the schedules registered against that cluster
