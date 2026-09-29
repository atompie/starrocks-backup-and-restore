## MODIFIED Requirements

### Requirement: Define a recurring backup schedule
The system SHALL allow an authenticated client to create a recurring schedule — one that
specifies a `cadence` — against a registered cluster identified by a cluster id in the request
path, specifying job type (full or incremental backup), an inventory group id, a repository to
back up into, a cadence (cron expression or equivalent interval), an optional execution backend
override, and — when the job type is full backup — a `retention` count of at least 1 (the number
of successful full backups this schedule keeps). The system SHALL reject creation with HTTP 404 if
the inventory group id does not exist on that cluster, SHALL reject creation with HTTP 404 if the
named repository does not exist on that cluster (via the same live StarRocks-side catalog lookup
used to validate a direct backup job's repository), SHALL reject creation with HTTP 422 if a
full-backup recurring schedule omits `retention`, SHALL reject creation with HTTP 422 if an
incremental-backup schedule specifies `retention` (incremental backups are never subject to
retention), and, once accepted, SHALL persist it in the metadata store, computing its next-run
time from the cadence. When a recurring full-backup job completes with status `SUCCESS`, the system
SHALL automatically submit an asynchronous `retention` job scoped to that schedule to enforce its
retention copy limit.

#### Scenario: Successful schedule creation
- **WHEN** an authenticated client creates a recurring full-backup schedule under a valid cluster
  id, with a valid inventory group id that exists on that cluster, a repository that exists on
  that cluster, a cadence, and a `retention` of at least 1
- **THEN** the system persists the schedule against that cluster, computes its next-run time, and
  returns the created schedule including the inventory group id, repository, and retention

#### Scenario: Schedule against an unknown cluster
- **WHEN** an authenticated client creates a schedule under a cluster id that is not registered
- **THEN** the system rejects the request with HTTP 404 and does not create a schedule

#### Scenario: Schedule against an unknown inventory group
- **WHEN** an authenticated client creates a schedule with an inventory group id that does not
  exist on the target cluster
- **THEN** the system rejects the request with HTTP 404 and does not create a schedule

#### Scenario: Schedule against an unknown repository
- **WHEN** an authenticated client creates a schedule naming a repository that does not exist on
  the target cluster
- **THEN** the system rejects the request with HTTP 404 and does not create a schedule

#### Scenario: Invalid cadence expression
- **WHEN** an authenticated client creates a recurring schedule with a cadence expression that
  cannot be parsed
- **THEN** the system rejects the request with a 422 validation error

#### Scenario: Recurring full-backup schedule missing retention
- **WHEN** an authenticated client creates a recurring full-backup schedule without a `retention`
  value
- **THEN** the system rejects the request with HTTP 422 and does not create a schedule

#### Scenario: Recurring incremental-backup schedule specifying retention
- **WHEN** an authenticated client creates a recurring incremental-backup schedule with a
  `retention` value set
- **THEN** the system rejects the request with HTTP 422 and does not create a schedule

#### Scenario: Retention is enforced after a recurring full backup succeeds
- **WHEN** a recurring full-backup job completes with status `SUCCESS`
- **THEN** the system submits an asynchronous `retention` job scoped to that schedule
