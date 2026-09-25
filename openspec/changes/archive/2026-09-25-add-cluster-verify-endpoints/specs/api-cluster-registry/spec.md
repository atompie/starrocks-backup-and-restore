## ADDED Requirements

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
