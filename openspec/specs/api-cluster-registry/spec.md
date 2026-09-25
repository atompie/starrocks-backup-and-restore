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

### Requirement: Verify cluster connection
The system SHALL allow an authenticated client to test connectivity to a StarRocks cluster, either by submitting connection parameters directly or by referencing a registered cluster's stored connection details, and SHALL report the result without creating, modifying, or deleting any cluster data. If a database name is supplied (directly or stored), the system SHALL also verify that database is accessible; if no database name is supplied, the system SHALL verify only the connection to the cluster server. The system SHALL NOT expose the plaintext or encrypted password in the verification response, in logs, in raised exceptions, or in OpenAPI documentation examples.

#### Scenario: Verify submitted connection parameters succeeds
- **WHEN** an authenticated client submits valid host, port, user, and password (and optionally a database) to the connection-verification endpoint
- **THEN** the system attempts a real connection using those parameters and returns a success result, without creating a cluster record

#### Scenario: Verify submitted connection parameters fails
- **WHEN** an authenticated client submits connection parameters that cannot reach or authenticate against a StarRocks cluster (unreachable host, invalid port, wrong credentials, connection timeout, or an inaccessible database)
- **THEN** the system returns a failure result describing the problem, without raising an unhandled error and without including the submitted password

#### Scenario: Verify submitted connection parameters with no database
- **WHEN** an authenticated client submits connection parameters with no database name
- **THEN** the system verifies only the connection to the cluster server and does not attempt to access any database

#### Scenario: Verify a registered cluster's connection succeeds
- **WHEN** an authenticated client requests verification for a registered cluster id whose stored connection details are currently valid
- **THEN** the system attempts a real connection using the cluster's stored host, port, user, password, and database, and returns a success result

#### Scenario: Verify a registered cluster's connection fails
- **WHEN** an authenticated client requests verification for a registered cluster id whose stored connection details no longer work
- **THEN** the system returns a failure result describing the problem, without modifying the stored cluster record

#### Scenario: Verify an unknown registered cluster
- **WHEN** an authenticated client requests verification for a cluster id that does not exist
- **THEN** the system responds with HTTP 404

#### Scenario: Verify a registered cluster whose stored password cannot be decrypted
- **WHEN** an authenticated client requests verification for a registered cluster whose stored password cannot be decrypted (e.g. the server's encryption key was rotated after the cluster was registered)
- **THEN** the system returns a failure result describing the problem, without raising an unhandled error and without attempting a network connection
