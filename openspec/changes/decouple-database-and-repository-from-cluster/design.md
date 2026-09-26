## Context

See `proposal.md` - Why. In short: `Cluster.database` and `Cluster.repository` (both required,
single-value columns on `clusters`) are used directly by `commands/backup.py`, `commands/restore.py`,
and `commands/prune.py` as *the* database and *the* repository for every operation against that
cluster, even though `TableInventory` already stores a `database_name` per table-membership row
(so a group can already span multiple databases) and `api-repository-management` already treats
repositories as live StarRocks catalog state, not a cluster attribute. `Cluster.default_backend`
and per-job `backend` are free-form strings today (`max_length=64`), checked only against the
server's *enabled* backends at request time - nothing rejects a typo or a backend that will never
be implemented.

## Goals / Non-Goals

**Goals:**
- Remove `database`/`repository` from `Cluster` and stop `commands/*.py` from reading them.
- Make full/incremental backup carry an explicit `repository`, validated live against the target
  cluster's repository catalog.
- Make restore infer repository from `backup_history` (unchanged pattern) and require `database`
  only when `table` (not `group_id`) is given.
- Make prune's `group_id` required, and infer each candidate snapshot's repository from
  `backup_history` rather than a request field.
- Constrain `default_backend`/`backend` to `{"thread", "job"}` at the schema layer, independent of
  the existing enabled-backend runtime check.

**Non-Goals:**
- Implementing an actual `"job"` execution backend (e.g. Kubernetes Job) - only enum validation for
  the value, so the API contract is ready when one is added.
- Changing how repositories are created/listed/deleted (`api-repository-management` is unaffected).
- Changing how inventory groups or table memberships work (`api-inventory-groups` is unaffected).

## Decisions

### `database` is deleted, not moved
`TableInventory.database_name` is already the per-table source of truth, and a group can already
span multiple databases (each row picks its own). Adding a "group default database" would create a
second, possibly-conflicting notion of "the" database. The only place a bare database name is
still genuinely needed is disambiguating a single bare `table` name in a restore request (there is
no group to look the table up against) - so `RestoreRequest.database` is added there, required iff
`table` is set, exactly mirroring the existing `group_id`/`table` mutual-exclusion validator.

`ClusterVerifyRequest.database` already exists and is already optional/independent of the stored
cluster record - it needs no change other than losing the "or stored" fallback, since there is no
longer a stored database to fall back to.

**Group database derivation (discovered during implementation).** `planner.find_tables_by_group`
already returns each table's own `database_name` per row, but every downstream planner function it
feeds into (`validate_tables_exist`, `build_full_backup_command`, `get_all_partitions_for_tables`,
`find_recent_partitions`, `build_incremental_backup_command`, `find_latest_full_backup`) takes a
single `database: str` for the whole operation - rewriting all of them to build one backup command
per distinct database within a group is out of scope for this change (it's a redesign of
`planner.py`'s command-building, not a field-source swap). `commands/backup.py` instead derives
"the database" for a group via a new helper that looks up the group's distinct `database_name`
values from `table_inventory`: if exactly one, it is used exactly as `cluster.database` was before;
if the group spans more than one, the backup fails fast with a clear error, rather than silently
skipping the mismatched rows - **this also fixes a pre-existing bug**, since today's
`validate_tables_exist(db, database, tables, group)` filters `tables` down to
`t["database"] == database` before validating, silently ignoring any table already in a different
database instead of erroring. True multi-database backup support within one group remains a
follow-up.

### `repository` becomes per-operation, resolved differently depending on the operation
Three different resolution strategies, chosen per how much information the operation already has:

| Operation | Repository source | Why |
|---|---|---|
| Full/incremental backup | New required `repository` field on the request | No prior backup exists yet to infer from - this is genuinely a client decision, validated live against `api-repository-management`'s existing repository-listing mechanism (same 404-on-unknown pattern already used for `group_id`). |
| Restore | `backup_history.repository`, looked up by `target_label` | The target backup already recorded which repository it lives in when it was created; asking the client to repeat it invites contradiction (client says repo X, backup is actually in repo Y) for no benefit. |
| Prune | `backup_history.repository`, looked up per candidate snapshot (via the existing `prune.get_successful_backups` group-scoped join) | Same reasoning as restore - each snapshot already knows its own repository. Because prune's `keep_last`/`older_than` strategies operate over a *set* of snapshots that could in principle span more than one repository (if a group's backups were ever redirected to a different repository over time), resolving repository per-snapshot rather than once per-request is the only way to describe this that doesn't assume single-repository-per-group. |

For the `snapshot`/`snapshots` (named) prune strategies, `commands/prune.py` currently calls
`prune.verify_snapshot_exists(database, cluster.repository, snap)` as a live pre-check *before*
filtering against the group-scoped `backup_history` list. Once `cluster.repository` is gone, this
becomes: look up each named snapshot's repository from `backup_history` (scoped to `cluster_id` and
`group_id`, same join as the other strategies), then still perform the live
`verify_snapshot_exists` check against that resolved repository. This preserves today's behavior of
catching a snapshot that was deleted out-of-band directly in StarRocks, rather than only failing
later at `DROP SNAPSHOT` time - the tradeoff decided against dropping the live check.

### `Schedule` gains its own `repository` (discovered during implementation)
`run_due_schedules` (`commands/schedules.py`) submits full/incremental backup jobs on a schedule's
behalf via `submit_job(session, cluster, schedule.job_type, {"group_id": schedule.inventory_group_id}, schedule.backend)`
- no repository in that params dict. Once `BackupFullRequest`/`BackupIncrementalRequest.repository`
is required, a schedule needs to carry that same decision itself, since there is no per-invocation
client to ask at run-due time. `Schedule` gains a required `repository` column, validated live
against the target cluster's repository list at schedule-creation/update time (the same 404-on-
unknown pattern used for `group_id` and for backup-job `repository`), and `run_due_schedules` passes
`schedule.repository` through in the job params it builds.

### `group_id` becomes required for prune
`prune.get_successful_backups` already accepts and applies `group=group` as an optional filter,
joining `backup_history` -> `backup_partitions` -> `table_inventory`. Making `group_id` required is
purely a validation change (reject `None`/missing with 422, same shape as the existing
`group_id`-required check on full/incremental backup) - no new join logic is needed, since the
group-scoped path is already the one all four strategies (`keep_last`, `older_than`, `snapshot`,
`snapshots`) use when a group is given.

### Backend enum is enforced at the schema layer, separately from the enabled-backends check
`ClusterCreate.default_backend`, `ClusterUpdate.default_backend`, and every job/schedule request's
`backend` field become a closed set (`Literal["thread", "job"]` or equivalent enum) instead of a
free `str`. This is deliberately a *different* check from `api/config.get_enabled_backends()` /
`_KNOWN_BACKEND_FACTORIES` (`api/app.py`), which governs whether a recognized backend is actually
runnable on this server today. Keeping them separate means:
- An unrecognized value (typo, future speculative name) is rejected immediately as a 422 schema
  error, the same way any other malformed field is.
- A recognized-but-not-yet-enabled value (`"job"`, until a Kubernetes-Job-style backend is
  implemented) continues to be rejected by the existing enabled-backends check, unchanged.

### Migration
A single Alembic migration drops `clusters.database` and `clusters.repository`
(`store/migrations/versions/`, following the existing `ff654976aa0e_inventory_groups_by_id.py` /
`059d2b79d525_move_ops_tables_into_sqlite.py` pattern). This is destructive for any already-stored
values - see Risks below.

## Risks / Trade-offs

- **[Risk]** Dropping `clusters.database`/`clusters.repository` discards any previously stored
  values with no automatic replacement, and every full/incremental backup job submitted afterward
  must now supply `repository` explicitly. -> **Mitigation**: this is a pre-1.0, internally-used
  tool (per existing changelog of breaking refactors in git history); call it out plainly in the
  proposal's Impact section and in the migration's revision message so operators know to update any
  saved request payloads/scripts before upgrading.
- **[Risk]** Resolving prune's repository per-snapshot (rather than once per-request) means a
  single prune request could in theory touch snapshots in more than one repository if a group's
  backups were ever redirected between repositories. -> **Mitigation**: this already reflects
  reality (each backup's repository was decided independently at backup time); the alternative
  (requiring one repository per prune request) would silently exclude legitimate prune targets that
  happen to sit in a different repository, which is worse.
- **[Trade-off]** Keeping the live `verify_snapshot_exists` check for named-snapshot prune (instead
  of trusting `backup_history` alone) costs one extra StarRocks round-trip per named snapshot, but
  preserves the existing guarantee that prune never attempts to drop something StarRocks doesn't
  actually have.

## Migration Plan

1. Add the Alembic migration dropping `clusters.database`/`clusters.repository`.
2. Update `store/models.py` (`Cluster`), `api/schemas.py` (`ClusterCreate`, `ClusterUpdate`,
   `ClusterRead`, `BackupFullRequest`, `BackupIncrementalRequest`, `RestoreRequest`, `PruneRequest`),
   and their routes (`api/routes/clusters.py`, `api/routes/jobs.py`) together, since the schema and
   route validation change in lockstep.
3. Update `commands/backup.py`, `commands/restore.py`, `commands/prune.py`, `commands/_shared.py`,
   and `api/routes/_cluster_connect.py` to stop reading `cluster.database`/`cluster.repository` and
   use the sources described above instead.
4. Update `cli.py` and the `api ...` CLI subcommands' flags to match the new required
   fields - no spec-level change to `cli-api-client` is needed (it already only requires that CLI
   commands call the corresponding API endpoints), but the flags themselves change.
5. No rollback beyond the standard Alembic downgrade is planned - restoring the columns would not
   restore the discarded values.

## Open Questions

(none - all decisions above were resolved during exploration rather than deferred.)
