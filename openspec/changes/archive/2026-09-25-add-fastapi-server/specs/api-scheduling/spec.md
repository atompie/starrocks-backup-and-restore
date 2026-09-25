## Purpose

Lets operators define recurring backup schedules per cluster/group through the API instead of maintaining external cron entries by hand, and lets a lightweight periodic trigger (cron, Kubernetes CronJob) ask the server to run whatever is due.

## ADDED Requirements

### Requirement: Define a recurring backup schedule
The system SHALL allow an authenticated client to create a schedule specifying a registered cluster, job type (full or incremental backup), inventory group, a cadence (cron expression or equivalent interval), and an optional execution backend override, and SHALL persist it in the metadata store.

#### Scenario: Successful schedule creation
- **WHEN** an authenticated client creates a schedule with a valid cluster id, job type, group, and cadence
- **THEN** the system persists the schedule, computes its next-run time, and returns the created schedule

#### Scenario: Schedule against an unknown cluster
- **WHEN** an authenticated client creates a schedule referencing a cluster id that is not registered
- **THEN** the system rejects the request with HTTP 404 and does not create a schedule

#### Scenario: Invalid cadence expression
- **WHEN** an authenticated client creates a schedule with a cadence expression that cannot be parsed
- **THEN** the system rejects the request with a 422 validation error

### Requirement: List, update, and remove schedules
The system SHALL allow an authenticated client to list all schedules, update a schedule's cadence/group/backend/enabled state, and delete a schedule.

#### Scenario: Disabling a schedule stops it from running
- **WHEN** an authenticated client updates a schedule's enabled flag to false
- **THEN** the system excludes that schedule from future due-schedule runs until it is re-enabled

#### Scenario: Deleting a schedule
- **WHEN** an authenticated client deletes an existing schedule
- **THEN** the system removes it from the registry and it is no longer considered for due-schedule runs

### Requirement: Running due schedules submits jobs through the standard job system
The system SHALL expose an endpoint that, when called, finds all enabled schedules whose next-run time has passed, submits a job for each through the same job-submission path used by direct API job submission (including backend selection), and advances each triggered schedule's next-run time.

#### Scenario: Due schedule is triggered
- **WHEN** the run-due endpoint is called and a schedule's next-run time has passed
- **THEN** the system submits a job for that schedule's cluster/job-type/group, and updates the schedule's next-run time to the next occurrence after now

#### Scenario: Not-yet-due schedule is skipped
- **WHEN** the run-due endpoint is called and a schedule's next-run time has not yet passed
- **THEN** the system does not submit a job for that schedule and leaves its next-run time unchanged

#### Scenario: No schedules are due
- **WHEN** the run-due endpoint is called and no enabled schedule is due
- **THEN** the system responds successfully indicating zero jobs were triggered

#### Scenario: Run-due call is idempotent per due occurrence
- **WHEN** the run-due endpoint is called twice in quick succession before a triggered job's schedule advances its next-run time
- **THEN** the second call does not submit a duplicate job for a schedule already triggered for its current due occurrence
