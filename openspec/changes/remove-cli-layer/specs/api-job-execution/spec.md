## MODIFIED Requirements

### Requirement: Job execution reuses existing backup/restore/prune behavior unchanged
The system SHALL produce the same backup labels, bookkeeping records (persisted in the tool's own
SQLite metastore rather than on the StarRocks side), and StarRocks-side snapshot behavior for a
given backup/restore/prune operation regardless of which execution backend runs it.

#### Scenario: API-submitted full backup is indistinguishable from a CLI backup
- **WHEN** the same full backup (same cluster, inventory group, and repository) is submitted via
  the API twice, once to each of two enabled execution backends
- **THEN** the resulting snapshot label, SQLite-backed `backup_history` record (scoped to that
  cluster), and repository snapshot are equivalent for the same inputs, regardless of which
  backend executed the operation
