## MODIFIED Requirements

### Requirement: Job execution reuses existing backup/restore/prune behavior unchanged
The system SHALL produce the same backup labels, bookkeeping records (persisted in the tool's own
SQLite metastore rather than on the StarRocks side), and StarRocks-side snapshot behavior for a job
submitted via the API as the equivalent existing CLI command produces, for any execution backend.

#### Scenario: API-submitted full backup is indistinguishable from a CLI backup
- **WHEN** a full backup is submitted via the API for a group that would otherwise be backed up via the CLI's `backup full` command
- **THEN** the resulting snapshot label, SQLite-backed `backup_history` record (scoped to that cluster), and repository snapshot match what the CLI command would have produced for the same inputs
