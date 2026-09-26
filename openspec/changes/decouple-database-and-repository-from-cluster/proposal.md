## Why

`Cluster.database` and `Cluster.repository` are required, single-value fields fixed at
cluster-registration time, but neither can actually be known at that point: which database(s) to
back up is decided per inventory group (whose table memberships already carry a `database_name`
per row, so a group can already span multiple databases), and which repository to use is decided
per job, since `api-repository-management` already treats repositories as StarRocks-side catalog
state discovered live, not something a cluster has exactly one of. Full/incremental backup
requests currently have no `repository` field at all and silently fall back to `cluster.repository`,
and prune can currently target every backup in the repository when no `group_id` is given, which
makes it too easy to prune more than intended. `Cluster.default_backend` is also an unconstrained
string today, so a typo or arbitrary value is only caught when a job tries to run.

## What Changes

- **BREAKING**: Remove `database` and `repository` from `Cluster` (registration, update, and read
  models). Cluster registration becomes connection details (name, host, port, user, password) plus
  `default_backend`.
- **BREAKING**: `default_backend` on `Cluster` (create/update) and `backend` on job/schedule
  requests are now validated against a closed set of `{"thread", "job"}`, rejecting anything else
  with HTTP 422. This is independent of, and does not replace, the existing check that a request's
  backend must also be enabled on the server.
- **BREAKING**: `BackupFullRequest` and `BackupIncrementalRequest` gain a required `repository`
  field, validated against that cluster's live repository list (StarRocks-side, via the same
  mechanism `api-repository-management` already uses to list repositories) - HTTP 404 if the named
  repository does not exist on that cluster, before any job is created.
- **BREAKING**: `RestoreRequest` gains a `database` field, required if and only if `table` is set
  (mirrors the existing group/table mutual-exclusion rule). Restore continues to infer which
  repository to use from the target backup's own recorded `backup_history.repository` - no new
  field needed there.
- **BREAKING**: `PruneRequest.group_id` becomes required (was optional), so prune always targets
  one inventory group's backups and can never sweep every backup in a repository at once. Each
  candidate snapshot's repository is resolved from its own `backup_history.repository` record
  (via the existing group-scoped join in `prune.get_successful_backups`) rather than a new request
  field; the named-snapshot strategies (`snapshot`/`snapshots`) resolve each name's repository the
  same way before verifying it still exists live in StarRocks.
- Backup/restore/prune execution (`commands/backup.py`, `commands/restore.py`,
  `commands/prune.py`) stops reading `cluster.database`/`cluster.repository` and instead derives
  database scope from the inventory group's table memberships and repository from the request or
  from `backup_history`, per operation above.
- **BREAKING**: `Schedule` gains a required `repository` field (discovered during implementation:
  `run_due_schedules` submits full/incremental backup jobs on a schedule's behalf, and those job
  types now require `repository` - a schedule needs its own copy of that decision, validated live
  against the target cluster's repository list the same way at schedule-creation time).

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `api-cluster-registry`: Cluster registration/update/read no longer includes `database` or
  `repository`; `default_backend` is constrained to `{"thread", "job"}`.
- `api-job-execution`: Full/incremental backup requests require an explicit `repository`, validated
  against the cluster's live repository list; restore requests require `database` when `table` is
  given; prune requests require `group_id`; per-job `backend` override is constrained to
  `{"thread", "job"}`.
- `api-scheduling`: Schedule creation requires a `repository`, validated against the cluster's live
  repository list the same way full/incremental backup job submission is.

## Impact

- **Schema/migration**: `alembic` migration dropping `clusters.database` and `clusters.repository`
  (new migration alongside `src/starrocks_br/store/migrations/versions/`).
- **API schemas** (`src/starrocks_br/api/schemas.py`): `ClusterCreate`, `ClusterUpdate`,
  `ClusterRead`, `BackupFullRequest`, `BackupIncrementalRequest`, `RestoreRequest`, `PruneRequest`.
- **Store model** (`src/starrocks_br/store/models.py`): `Cluster` loses `database`/`repository`
  columns.
- **Routes**: `api/routes/clusters.py`, `api/routes/jobs.py`, `api/routes/schedules.py` (repository
  validation against live StarRocks catalog, mirroring existing `group_id` validation).
- **Execution**: `commands/backup.py`, `commands/restore.py`, `commands/prune.py`,
  `commands/_shared.py`, `api/routes/_cluster_connect.py`, `cli.py` (`cluster.database` used for
  connection default database - removed or replaced).
- **CLI** (`cli.py` and any `api ...` client subcommands): gains flags for the new required
  `repository`/`database` fields; no requirement-level change to `cli-api-client`, since it already
  specifies only that CLI commands call the corresponding API endpoints.
- Existing registered clusters in any deployed metastore lose their stored `database`/`repository`
  values on migration; operators must supply `repository` on every backup job and (where
  applicable) `database` on table-scoped restores going forward.
