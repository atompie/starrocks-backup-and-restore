# api-cluster-registry Specification

## Purpose

Lets operators register, inspect, and remove StarRocks clusters (connection details and backup repository) through the API, so backup/restore/schedule operations can target any registered cluster by id instead of a per-invocation config file.

## Requirements

### Requirement: Register a new cluster
The system SHALL allow an authenticated client to register a new StarRocks cluster by providing a unique name, host, port, user, password, and backup repository name, and SHALL persist the connection details (with the password encrypted at rest) in the metadata store.

#### Scenario: Successful registration
- **WHEN** an authenticated client submits a new cluster with a unique name and valid connection fields
- **THEN** the system persists the cluster, encrypts the stored password, and returns the created cluster's id and metadata (never the plaintext password)

#### Scenario: Duplicate cluster name is rejected
- **WHEN** an authenticated client submits a cluster whose name already exists in the registry
- **THEN** the system rejects the request with a 409 conflict and does not create a duplicate entry

#### Scenario: Missing required field is rejected
- **WHEN** an authenticated client submits a cluster missing a required connection field (host, port, user, or repository)
- **THEN** the system rejects the request with a 422 validation error and does not persist a partial record

#### Scenario: Empty password is accepted
- **WHEN** an authenticated client registers a cluster with an empty password (StarRocks permits passwordless users, e.g. a local root)
- **THEN** the system accepts and persists the cluster

### Requirement: List and inspect registered clusters
The system SHALL allow an authenticated client to list all registered clusters and retrieve a single cluster's details by id, and SHALL never return the plaintext or encrypted password in any response.

#### Scenario: List registered clusters
- **WHEN** an authenticated client requests the list of clusters
- **THEN** the system returns all registered clusters with their id, name, host, port, user, and repository, excluding password material

#### Scenario: Retrieve an unknown cluster
- **WHEN** an authenticated client requests a cluster id that does not exist
- **THEN** the system responds with HTTP 404

### Requirement: Update a registered cluster's connection details
The system SHALL allow an authenticated client to update a registered cluster's connection fields (host, port, user, password, repository), re-encrypting a new password when provided.

#### Scenario: Update succeeds
- **WHEN** an authenticated client submits updated connection fields for an existing cluster id
- **THEN** the system persists the changes and subsequent jobs against that cluster use the updated connection details

### Requirement: Remove a registered cluster
The system SHALL allow an authenticated client to remove a registered cluster, and SHALL reject removal while the cluster has jobs in a non-terminal state or schedules still enabled against it.

#### Scenario: Removal succeeds when idle
- **WHEN** an authenticated client deletes a cluster with no running jobs and no enabled schedules
- **THEN** the system removes the cluster from the registry

#### Scenario: Removal blocked by active jobs
- **WHEN** an authenticated client attempts to delete a cluster that has a job in PENDING or RUNNING state
- **THEN** the system rejects the deletion with a 409 conflict and leaves the cluster registered
