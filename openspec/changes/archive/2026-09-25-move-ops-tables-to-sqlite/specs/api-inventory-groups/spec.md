## MODIFIED Requirements

### Requirement: Inventory groups can be listed
The system SHALL allow an authenticated client to list all inventory groups that currently exist
on a registered cluster, reflecting the tool's own SQLite-backed, `cluster_id`-scoped
`table_inventory` state at request time, including each group's name and the number of
table-membership rows it has.

#### Scenario: Listing groups on a cluster with existing groups
- **WHEN** an authenticated client lists inventory groups for a registered cluster that has groups
  `prod` (2 rows) and `staging` (1 row)
- **THEN** the system responds with HTTP 200 and both groups with their respective table counts

#### Scenario: Listing groups against an unknown cluster
- **WHEN** an authenticated client lists inventory groups for a cluster id that is not registered
- **THEN** the system responds with HTTP 404
