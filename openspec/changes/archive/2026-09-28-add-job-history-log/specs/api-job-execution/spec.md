## ADDED Requirements

### Requirement: A job's execution history can be retrieved
The system SHALL expose an endpoint to retrieve the append-only execution history of a backup or
restore job, returned as an ordered list from oldest to newest, each entry carrying the status
recorded at that point in time, a timestamp, and an optional message and detail payload. History
entries are never modified or removed once recorded; the endpoint always reflects every entry
recorded so far for that job.

#### Scenario: Retrieving history for a completed backup job
- **WHEN** an authenticated client requests the history of a backup job that has finished
- **THEN** the system responds with HTTP 200 and a time-ordered list of that job's recorded
  status entries, ending with its final `SUCCESS` or `FAILED` entry

#### Scenario: Retrieving history for a running job
- **WHEN** an authenticated client requests the history of a job that is still `RUNNING`
- **THEN** the system responds with HTTP 200 and the entries recorded so far, without a terminal
  `SUCCESS`/`FAILED` entry

#### Scenario: Retrieving history for an unknown job
- **WHEN** an authenticated client requests the history of a job id that does not exist
- **THEN** the system responds with HTTP 404

#### Scenario: Repeated identical status is not duplicated
- **WHEN** the underlying StarRocks operation reports the same status on consecutive polls
- **THEN** the job's history contains only one entry for that status, not one entry per poll
