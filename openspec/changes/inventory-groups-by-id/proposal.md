## Why

Inventory groups have no identity of their own today — a "group" is just a repeated name string on
`table_inventory` rows, and every consumer (schedules, backup/restore/prune jobs, the REST API) carries
that name string around as its only reference. Names are not a stable identifier: they can collide
across intent, can't be renamed without rewriting every referencing row, and force every downstream
component to match on a free-form string instead of a real key. Schedules already avoid this problem by
being identified with a surrogate `id`; inventory groups should follow the same pattern so that backups,
restores, prunes, and schedules all reference a group by its id rather than its name. No inventory data
exists in the database yet, so this can be a clean, breaking cutover rather than a compatibility-preserving
migration.

## What Changes

- **BREAKING**: Introduce a real `inventory_groups` table with a surrogate `id` primary key
  (`cluster_id`, `name`, unique per cluster). Inventory groups are no longer just a string repeated across
  `table_inventory` rows.
- **BREAKING**: `table_inventory.inventory_group` (string) is replaced with `table_inventory.inventory_group_id`
  (FK to `inventory_groups.id`).
- **BREAKING**: `schedules.group_name` (string) is replaced with `schedules.inventory_group_id` (FK to
  `inventory_groups.id`).
- **BREAKING**: The inventory-groups REST API moves from name-keyed paths (`/groups/{group_name}/...`) to
  id-keyed paths (`/groups/{group_id}/...`). Group creation takes a `name` in the request body and returns
  the assigned `id`.
- **BREAKING**: Backup, restore, and prune job requests (`BackupFullRequest`, `BackupIncrementalRequest`,
  `RestoreRequest`, `PruneRequest`) accept a `group_id` (int) instead of `group` (str). Persisted job
  `params_json` stores the group id, not its name.
- **BREAKING**: Schedule create/update requests accept `inventory_group_id` (int) instead of `group_name`
  (str).
- Planner and restore/prune query helpers (`find_tables_by_group`, `find_recent_partitions_by_group`,
  `build_full_backup_command`, and the group filters in `restore.py`/`prune.py`) filter by
  `inventory_group_id` instead of matching on the name string.
- The direct-to-StarRocks CLI (`backup incremental/full --group`, `restore --group`, `prune --group`,
  `schedule add --group`) keeps accepting a human-readable group **name** at the command line, and resolves
  it to the group's id (scoped to the target cluster) before calling the command/API layer, which now only
  deals in ids. An unresolvable name fails the CLI invocation with a clear error before any job is created.
- The YAML `table_inventory:` config section used by `config init` stays name-keyed for authoring, but the
  bootstrap process creates/looks up the corresponding `inventory_groups` row and writes id-referencing
  `table_inventory` rows.
- No data migration/backfill logic is included: the database has no existing inventory or schedule rows, so
  this ships as a straight schema replacement rather than an in-place upgrade path.

## Capabilities

### New Capabilities
(none — inventory groups become id-identified within the existing `api-inventory-groups` capability rather
than introducing a new one)

### Modified Capabilities
- `api-inventory-groups`: groups are created with a name but identified, listed, retrieved, and deleted by
  id; all inventory-group endpoints move from name-keyed to id-keyed paths.
- `api-scheduling`: schedules reference an inventory group by id (`inventory_group_id`) instead of by name
  (`group_name`), including in create/update requests and in the group submitted with due-schedule job runs.
- `api-job-execution`: backup/restore/prune job submission validates and stores an inventory `group_id`
  rather than a `group` name string, including the "group must exist" and "group required for backup" checks.
- `cli-api-client`: schedule-add and job-submission CLI commands keep taking a `--group <name>` flag, but
  resolve it to the group's id against the target cluster before calling the API, and fail clearly when the
  name does not resolve.

## Impact

- **Schema/migrations**: new migration adding `inventory_groups`; `table_inventory.inventory_group` →
  `inventory_group_id`; `schedules.group_name` → `inventory_group_id`. Affects
  `src/starrocks_br/store/models.py` and a new file under `src/starrocks_br/store/migrations/versions/`.
- **API**: `src/starrocks_br/api/routes/inventory_groups.py`, `schedules.py`, and `jobs.py` (the
  `group_exists` check on backup submission, plus restore/prune submission);
  `src/starrocks_br/api/schemas.py` (`BackupFullRequest`, `BackupIncrementalRequest`, `RestoreRequest`,
  `PruneRequest`, `ScheduleCreate`, `ScheduleUpdate`, `ScheduleRead`).
- **Core logic**: `src/starrocks_br/inventory_groups.py`, `src/starrocks_br/planner.py`,
  `src/starrocks_br/restore.py`, `src/starrocks_br/prune.py`, `src/starrocks_br/commands/backup.py`,
  `restore.py`, `prune.py`, `schedules.py`.
- **CLI**: `src/starrocks_br/cli.py` (name→id resolution for `--group`), `src/starrocks_br/cli_api/job.py`,
  `src/starrocks_br/cli_api/schedule.py`.
- **Config**: `src/starrocks_br/config.py` (table_inventory bootstrap creates/resolves group ids).
- **Tests**: `tests/test_api_jobs.py`, `tests/test_commands_backup.py`, `tests/test_commands_restore.py`,
  `tests/test_commands_schedules.py`, `tests/test_planner.py`, `tests/test_inventory_groups_sql.py`,
  `tests/test_config.py`, `tests/integration/test_full_backup_restore_cycle.py`, plus schedule/inventory API
  and CLI-client tests.
- **No compatibility shims**: name-based fields, routes, and lookups are removed rather than deprecated
  alongside the new id-based ones.
