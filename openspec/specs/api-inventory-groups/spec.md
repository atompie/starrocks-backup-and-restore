# api-inventory-groups Specification

## Purpose

Lets operators create, discover, inspect, and delete inventory groups (named sets of
database/table memberships that scope backup, restore, and prune operations) on a registered
cluster through the API, instead of hand-writing SQL against `table_inventory` or relying on a
one-shot config bootstrap.

## Requirements

### Requirement: Inventory groups can be listed
The system SHALL allow an authenticated client to list all inventory groups that currently exist
on a registered cluster, reflecting the cluster's own `table_inventory` state live at request
time, including each group's name and the number of table-membership rows it has.

#### Scenario: Listing groups on a cluster with existing groups
- **WHEN** an authenticated client lists inventory groups for a registered cluster that has groups
  `prod` (2 rows) and `staging` (1 row)
- **THEN** the system responds with HTTP 200 and both groups with their respective table counts

#### Scenario: Listing groups against an unknown cluster
- **WHEN** an authenticated client lists inventory groups for a cluster id that is not registered
- **THEN** the system responds with HTTP 404

### Requirement: Inventory groups can be created with an initial set of tables
The system SHALL allow an authenticated client to create a new inventory group on a registered
cluster by providing a group name and at least one database/table membership, SHALL reject
creation if the group name already has any existing rows, and SHALL validate every provided value
safely (no raw SQL interpolation of client-supplied strings).

#### Scenario: Creating a new group
- **WHEN** an authenticated client creates a group named `prod` with one membership
  (database `sales`, table `*`) on a registered cluster with no existing `prod` group
- **THEN** the system responds with HTTP 201 and the group is subsequently visible via list/get

#### Scenario: Creating a group whose name already exists
- **WHEN** an authenticated client creates a group using a name that already has rows on that
  cluster
- **THEN** the system responds with HTTP 409 and does not modify existing rows

### Requirement: A single inventory group's table memberships can be retrieved
The system SHALL allow an authenticated client to retrieve all database/table memberships for a
named inventory group on a registered cluster, and SHALL respond with 404 if the group has no
rows.

#### Scenario: Retrieving an existing group
- **WHEN** an authenticated client requests group `prod` on a cluster where `prod` has two
  memberships
- **THEN** the system responds with HTTP 200 and both memberships, each with database, table, and
  timestamps

#### Scenario: Retrieving an unknown group
- **WHEN** an authenticated client requests a group name that has no rows on that cluster
- **THEN** the system responds with HTTP 404

### Requirement: Table memberships can be added to and removed from a group
The system SHALL allow an authenticated client to add a single database/table membership to a
named inventory group, SHALL reject the addition with a conflict if that exact membership already
exists, and SHALL allow removal of a single membership, responding with 404 if that membership
does not exist.

#### Scenario: Adding a new membership
- **WHEN** an authenticated client adds membership (database `sales`, table `orders`) to group
  `prod`, which does not already have that membership
- **THEN** the system responds with HTTP 201 and the membership is subsequently visible when the
  group is retrieved

#### Scenario: Adding a duplicate membership
- **WHEN** an authenticated client adds a database/table membership to a group that already has
  that exact membership
- **THEN** the system responds with HTTP 409 and does not create a duplicate row

#### Scenario: Removing an existing membership
- **WHEN** an authenticated client removes a membership that exists on the group
- **THEN** the system responds with HTTP 204 and the membership is no longer visible when the
  group is retrieved

#### Scenario: Removing a nonexistent membership
- **WHEN** an authenticated client removes a database/table membership that does not exist on the
  group
- **THEN** the system responds with HTTP 404

### Requirement: An entire inventory group can be deleted
The system SHALL allow an authenticated client to delete an inventory group and all of its table
memberships in one operation, and SHALL respond with 404 if the group has no rows to delete.

#### Scenario: Deleting an existing group
- **WHEN** an authenticated client deletes group `prod`, which has existing memberships
- **THEN** the system responds with HTTP 204 and the group no longer appears in the list endpoint

#### Scenario: Deleting an unknown group
- **WHEN** an authenticated client deletes a group name that has no rows on that cluster
- **THEN** the system responds with HTTP 404
