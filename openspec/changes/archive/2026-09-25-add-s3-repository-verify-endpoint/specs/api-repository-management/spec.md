## ADDED Requirements

### Requirement: Verify S3 credentials and reachability independent of any cluster
The system SHALL allow an authenticated client to verify that a given S3 endpoint is reachable and that the given credentials are valid, without requiring or connecting to any registered StarRocks cluster, and SHALL respond with a clear success/failure result rather than an unhandled server error for any expected failure mode (unreachable endpoint, invalid credentials, missing bucket, malformed location).

#### Scenario: Endpoint reachable and credentials valid
- **WHEN** an authenticated client submits a location, endpoint, and valid credentials for verification
- **THEN** the system reports success

#### Scenario: Credentials are invalid
- **WHEN** an authenticated client submits a location, endpoint, and credentials that the endpoint rejects as unauthorized
- **THEN** the system reports failure with a message indicating an authentication/authorization problem, and responds with HTTP 200 rather than a server error

#### Scenario: Bucket does not exist
- **WHEN** an authenticated client submits a location whose bucket does not exist at the given endpoint, with otherwise valid credentials
- **THEN** the system reports failure with a message indicating the bucket was not found, and responds with HTTP 200 rather than a server error

#### Scenario: Endpoint is unreachable
- **WHEN** an authenticated client submits an endpoint that cannot currently be reached from the API server
- **THEN** the system reports failure with a message indicating the endpoint could not be reached, and responds with HTTP 200 rather than a server error

#### Scenario: Submitted credentials are never echoed back
- **WHEN** an authenticated client submits a verification request, regardless of outcome
- **THEN** the system's response never includes the submitted secret key, in either the success or failure message

#### Scenario: Malformed location is rejected without a server error
- **WHEN** an authenticated client submits a location that does not identify a bucket
- **THEN** the system reports failure without raising an unhandled exception
