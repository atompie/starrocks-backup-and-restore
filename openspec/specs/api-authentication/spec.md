# api-authentication Specification

## Purpose

Protects every API endpoint with a shared secret so the FastAPI server can be exposed beyond localhost without granting anonymous access to cluster credentials or backup/restore operations.

## Requirements

### Requirement: API requests must present a valid bearer token
The system SHALL require every API request (except a health-check endpoint) to include an `Authorization: Bearer <token>` header matching the server's configured API key, and SHALL reject requests that omit or mismatch it with an HTTP 401 response.

#### Scenario: Request without a token is rejected
- **WHEN** a client calls any protected endpoint without an `Authorization` header
- **THEN** the server responds with HTTP 401 and does not perform the requested operation

#### Scenario: Request with an incorrect token is rejected
- **WHEN** a client calls a protected endpoint with an `Authorization: Bearer <token>` header whose token does not match the configured API key
- **THEN** the server responds with HTTP 401 and does not perform the requested operation

#### Scenario: Request with the correct token succeeds
- **WHEN** a client calls a protected endpoint with an `Authorization: Bearer <token>` header whose token matches the configured API key
- **THEN** the server processes the request normally

### Requirement: API key is configured out-of-band, never over the API
The system SHALL read the API key from server-side configuration (environment variable or startup config), and SHALL NOT expose any endpoint to read, list, or rotate the API key value.

#### Scenario: Server refuses to start without a configured key
- **WHEN** the API server starts without an API key configured
- **THEN** the server fails to start and logs an error explaining that an API key must be configured

### Requirement: Health check is reachable without authentication
The system SHALL expose a health-check endpoint that does not require the bearer token, reporting server liveness only (no cluster or job data).

#### Scenario: Health check succeeds without a token
- **WHEN** a client calls the health-check endpoint without an `Authorization` header
- **THEN** the server responds with HTTP 200 and a liveness indicator, without requiring authentication
