## MODIFIED Requirements

### Requirement: A job's execution history can be retrieved
The system SHALL expose an endpoint to retrieve the append-only execution history of a backup or
restore job, returned as an ordered list from oldest to newest, each entry carrying the status
recorded at that point in time, a timestamp, and an optional message and detail payload. History
entries are never modified or removed once recorded; the endpoint always reflects every entry
recorded so far for that job. A backup job that ends in status `FAILED` SHALL always have at least
one `FAILED` history entry, carrying the error message that caused the failure, regardless of
whether the failure occurred while a StarRocks backup operation was submitted or polled, or at any
other point in the job's execution.

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

#### Scenario: A backup job that fails before submitting to StarRocks still records why
- **WHEN** a full or incremental backup job fails for a reason unrelated to StarRocks backup
  submission or polling (for example, no matching tables are found, or recording backup
  references fails)
- **THEN** the job ends with status `FAILED` and its history contains a `FAILED` entry carrying
  the error message that caused the failure

#### Scenario: A backup job that fails to submit to StarRocks still records why
- **WHEN** a full or incremental backup job's StarRocks backup command fails to submit (for
  example, because a snapshot with that name already exists)
- **THEN** the job ends with status `FAILED` and its history contains a `FAILED` entry carrying
  the error message that caused the submission failure, rather than an empty history
