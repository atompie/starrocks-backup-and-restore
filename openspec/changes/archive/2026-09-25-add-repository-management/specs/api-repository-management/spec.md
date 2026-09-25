## Purpose

Lets operators list, create, and safely delete StarRocks backup repositories on a registered cluster through the API/CLI, instead of hand-writing `CREATE REPOSITORY`/`DROP REPOSITORY` SQL, while keeping repository storage credentials out of this tool's own metadata store.

## ADDED Requirements

### Requirement: List repositories on a registered cluster
The system SHALL allow an authenticated client to list all backup repositories that currently exist on a registered cluster, reflecting StarRocks' own repository catalog live at request time (not a locally cached copy), including each repository's name, storage location, broker, read-only flag, and any error StarRocks reports for it.

#### Scenario: List repositories
- **WHEN** an authenticated client requests the repository list for a registered cluster
- **THEN** the system connects to that cluster, queries its repository catalog, and returns every repository found with its name, location, broker, read-only flag, and error status

#### Scenario: List against an unknown cluster
- **WHEN** an authenticated client requests the repository list for a cluster id that is not registered
- **THEN** the system responds with HTTP 404 and does not attempt to connect anywhere

#### Scenario: Cluster is unreachable
- **WHEN** an authenticated client requests the repository list for a registered cluster that cannot currently be connected to
- **THEN** the system responds with an error distinguishing "cluster unreachable" from "no repositories found", rather than returning an empty list

### Requirement: Create an S3-compatible repository on a registered cluster
The system SHALL allow an authenticated client to create a new S3-compatible backup repository on a registered cluster by providing a repository name, storage location, and S3 connection details (access key, secret key, endpoint, and region), and SHALL NOT persist those credentials in its own metadata store.

#### Scenario: Successful creation
- **WHEN** an authenticated client submits a new repository name, S3 location, and valid S3 credentials for a registered cluster
- **THEN** the system creates the repository on that cluster and returns confirmation that it now exists, without storing the submitted credentials anywhere in the API's own metadata store

#### Scenario: Duplicate repository name
- **WHEN** an authenticated client attempts to create a repository whose name already exists on that cluster
- **THEN** the system rejects the request with a 409 conflict and does not attempt to recreate it

#### Scenario: Creation against an unknown cluster
- **WHEN** an authenticated client attempts to create a repository on a cluster id that is not registered
- **THEN** the system responds with HTTP 404 and does not create anything

#### Scenario: Missing required field is rejected
- **WHEN** an authenticated client submits a repository creation request missing a required field (name, location, access key, secret key, or endpoint)
- **THEN** the system rejects the request with a 422 validation error and does not create a partial or broken repository

### Requirement: Deleting a repository is blocked while it holds any snapshot
The system SHALL, before deleting a repository, check the target StarRocks cluster for any existing snapshot in that repository, and SHALL refuse the deletion if any snapshot is found, regardless of what this tool's own backup history records show.

#### Scenario: Deletion blocked by an existing snapshot
- **WHEN** an authenticated client attempts to delete a repository that StarRocks reports as containing at least one snapshot
- **THEN** the system rejects the deletion with a 409 conflict, explains that the repository still holds snapshot data, and does not delete it

#### Scenario: Deletion succeeds when empty
- **WHEN** an authenticated client attempts to delete a repository that StarRocks reports as containing no snapshots
- **THEN** the system deletes the repository from that cluster's catalog

#### Scenario: Deletion against an unknown cluster or repository
- **WHEN** an authenticated client attempts to delete a repository on a cluster id that is not registered, or a repository name that does not exist on that cluster
- **THEN** the system responds with HTTP 404 and does not delete anything

### Requirement: CLI can list, create, and delete repositories
The system SHALL provide CLI commands to list, create, and delete repositories on a registered cluster, by calling the corresponding API endpoints, consistent with the existing `api cluster`/`api job`/`api schedule` command groups.

#### Scenario: Create a repository via CLI
- **WHEN** a user runs the repository-creation CLI command with valid S3 connection details for a registered cluster
- **THEN** the CLI calls the API to create the repository and confirms it was created

#### Scenario: Delete a repository blocked by existing snapshots via CLI
- **WHEN** a user runs the repository-deletion CLI command against a repository that still holds snapshots
- **THEN** the CLI surfaces the API's 409 conflict and exits with a non-zero status, without retrying automatically
