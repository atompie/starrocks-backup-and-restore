# cli-api-client Specification

## Purpose

Gives operators a command-line way to drive the FastAPI server (register clusters, trigger and check jobs, and run due schedules from cron/Kubernetes CronJob) without hand-writing HTTP requests, as a separate surface from the existing direct-to-StarRocks CLI commands.

## Requirements

### Requirement: CLI commands authenticate against a configured API server
The system SHALL provide CLI commands that call the FastAPI server's endpoints using a configured server URL and API key (via flags and/or environment variables), and SHALL fail with a clear error when either is missing.

#### Scenario: Missing API key
- **WHEN** a user runs an API-client CLI command without an API key configured (flag or environment variable)
- **THEN** the CLI exits with a non-zero status and an error explaining that an API key must be provided

#### Scenario: Server rejects the request
- **WHEN** the configured API key is rejected by the server with HTTP 401
- **THEN** the CLI exits with a non-zero status and surfaces the server's authentication failure to the user

### Requirement: CLI can manage the cluster registry
The system SHALL provide CLI commands to register a new cluster, list registered clusters, and remove a cluster, by calling the corresponding API endpoints.

#### Scenario: Register a cluster via CLI
- **WHEN** a user runs the cluster-registration CLI command with valid connection details
- **THEN** the CLI calls the API to create the cluster and prints the created cluster's id

### Requirement: CLI can submit and monitor jobs via the API
The system SHALL provide CLI commands to submit a backup/restore/prune job against a registered cluster and to check a job's status/progress, by calling the corresponding API endpoints, and SHALL support waiting (polling) until the job reaches a terminal state when requested.

#### Scenario: Submit and wait
- **WHEN** a user runs the job-submission CLI command with a wait flag
- **THEN** the CLI submits the job via the API, polls its status until it reaches SUCCESS or FAILED, and exits with a status code reflecting the outcome

### Requirement: CLI can manage schedules against a specific cluster
The system SHALL provide CLI commands to create a schedule against a specific cluster, list the schedules registered against a specific cluster, and remove a schedule, by calling the corresponding cluster-scoped API endpoints.

#### Scenario: Add a schedule for a cluster via CLI
- **WHEN** a user runs the schedule-add CLI command with a cluster id, job type, group, and cadence
- **THEN** the CLI calls the API to create the schedule under that cluster and prints the created schedule's id and next run time

#### Scenario: List a cluster's schedules via CLI
- **WHEN** a user runs the schedule-list CLI command with a cluster id
- **THEN** the CLI calls the API to list only the schedules registered against that cluster

### Requirement: CLI provides a single-shot schedule runner for cron/CronJob use
The system SHALL provide a CLI command that calls the API's run-due-schedules endpoint once and exits, suitable for invocation from an external scheduler (cron, Kubernetes CronJob) on a fixed interval, and SHALL exit non-zero if the API call fails.

#### Scenario: Cron-driven invocation with due schedules
- **WHEN** the schedule-runner CLI command is invoked and the API reports schedules were triggered
- **THEN** the CLI exits with status 0 and reports how many jobs were triggered

#### Scenario: Cron-driven invocation with an unreachable API
- **WHEN** the schedule-runner CLI command is invoked and the API server cannot be reached
- **THEN** the CLI exits with a non-zero status and a clear error, so the invoking cron/CronJob can be alerted on
