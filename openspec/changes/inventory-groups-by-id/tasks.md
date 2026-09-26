## 1. Schema and migration

- [x] 1.1 Add `InventoryGroup` model (`id`, `cluster_id` FK, `name`, timestamps, `UniqueConstraint(cluster_id, name)`) to `src/starrocks_br/store/models.py`
- [x] 1.2 Change `TableInventory.inventory_group` (str) to `inventory_group_id` (FK to `inventory_groups.id`), update its `UniqueConstraint`/`Index` to use `inventory_group_id`, in `src/starrocks_br/store/models.py`
- [x] 1.3 Change `Schedule.group_name` (str) to `inventory_group_id` (FK to `inventory_groups.id`, `ondelete="RESTRICT"`) in `src/starrocks_br/store/models.py`
- [x] 1.4 Write a new Alembic migration creating `inventory_groups`, altering `table_inventory` and `schedules` accordingly, and verify `alembic upgrade head` succeeds against a fresh database
- [x] 1.5 Verify `alembic downgrade -1` cleanly reverses the new revision

## 2. Core inventory-group logic

- [x] 2.1 Rework `src/starrocks_br/inventory_groups.py`: `create_group` inserts/reuses an `inventory_groups` row and returns its id before inserting `table_inventory` rows; `list_groups`, `get_group`, `_membership_exists`, `add_membership`, `add_memberships_bulk`, `remove_membership`, `delete_group` take/return `group_id` instead of `group_name`
- [x] 2.2 Add `get_group_id_by_name(session, cluster_id, name)` helper for CLI-side name resolution; verify it raises a clear not-found error when no match exists on that cluster
- [x] 2.3 Make `delete_group` reject deletion with a clear error (naming the blocking schedule id(s)) when any schedule still references the group id, and verify with a unit test
- [x] 2.4 Update `src/starrocks_br/planner.py` (`find_tables_by_group`, `find_recent_partitions_by_group`, `build_full_backup_command`) to filter/accept `group_id` instead of `group_name`
- [x] 2.5 Update `src/starrocks_br/restore.py` and `src/starrocks_br/prune.py` group filters to use `inventory_group_id`

## 3. API layer

- [x] 3.1 Update `src/starrocks_br/api/routes/inventory_groups.py` to use `group_id` path params instead of `group_name`, keep `name` as a create-time request field, and return `id` in all responses; verify against `tests/test_api_*` for this route
- [x] 3.2 Update `src/starrocks_br/api/schemas.py`: `BackupFullRequest`, `BackupIncrementalRequest`, `RestoreRequest`, `PruneRequest` gain `group_id: int` (replacing `group: str`); `ScheduleCreate`/`ScheduleUpdate`/`ScheduleRead` gain `inventory_group_id: int` (replacing `group_name: str`)
- [x] 3.3 Update `src/starrocks_br/api/routes/schedules.py` to validate the inventory group id exists on the target cluster (404 if not) and to pass `inventory_group_id` through to job dispatch (`src/starrocks_br/commands/schedules.py`)
- [x] 3.4 Update backup/restore/prune job-submission routes to validate `group_id` existence (404) and required-ness (422) per the updated `api-job-execution` spec, and to store `group_id` in `Job.params_json`

## 4. Command layer

- [x] 4.1 Update `src/starrocks_br/commands/backup.py`, `restore.py`, `prune.py` to read `params.get("group_id")` instead of `params.get("group")`
- [x] 4.2 Update `src/starrocks_br/commands/schedules.py` dispatch to submit jobs with `{"group_id": schedule.inventory_group_id}`

## 5. Direct-to-StarRocks CLI (`cli.py`)

- [x] 5.1 Keep `--group <name>` on `backup incremental`, `backup full`, `restore`, `prune`; resolve the name to an id via `inventory_groups.get_group_id_by_name` before building command params, and exit non-zero with a clear error if unresolved
- [x] 5.2 Update `config.py`'s `table_inventory:` YAML bootstrap (`config init`) to create/resolve the named group into an `inventory_groups` row and write `inventory_group_id`-referencing `table_inventory` rows (satisfied by `bootstrap_table_inventory`'s rework in task 2.1 - `config.py` itself already emitted (group name, database, table) tuples, no change needed there)

## 6. HTTP CLI client (`cli_api/*.py`)

- [x] 6.1 Update `src/starrocks_br/cli_api/job.py` `--group` handling: list groups via the API, match by name client-side, resolve to `group_id` before submitting; fail clearly if unresolved
- [x] 6.2 Update `src/starrocks_br/cli_api/schedule.py` `--group` handling the same way, sending `inventory_group_id` to the schedule-create API call

## 7. Tests

- [x] 7.1 Update `tests/test_inventory_groups_sql.py` to the id-based contract (create returns id; list/get/add/remove/delete take id)
- [x] 7.2 Update `tests/test_planner.py` group-related cases to pass `group_id` instead of the literal name string
- [x] 7.3 Update `tests/test_commands_backup.py`, `tests/test_commands_restore.py`, `tests/test_commands_schedules.py` to use `group_id`/`inventory_group_id` and verify the passthrough into job params
- [x] 7.4 Update `tests/test_api_jobs.py` backup/restore/prune cases to submit `group_id`, including the "unknown group id" 404 and "missing group id" 422 cases (also rewrote `tests/test_api_inventory_groups.py` and `tests/test_api_schedules.py`, which exercise the same id-keyed routes, to real DB-backed flows - not originally itemized here but covered by the same `test_api_*` verification this task calls for)
- [x] 7.5 Update `tests/test_config.py` table_inventory-section tests for the id-resolving bootstrap behavior (name in YAML still validated as before; underlying rows now id-referencing) - verified unchanged/passing as-is since `config.py` itself is name-keyed and untouched (id resolution lives in `bootstrap_table_inventory`, covered by task 2.1/7.1 tests)
- [x] 7.6 Update `tests/integration/test_full_backup_restore_cycle.py` to create a group by name, capture the returned id, and use that id for backup/restore/prune calls (syntax-checked; requires a real StarRocks/S3 cluster to execute, not available in this environment)
- [x] 7.7 Add/verify CLI-level tests (both `cli.py` and `cli_api/*.py`) covering: successful name-to-id resolution, and a clear failure when the name does not resolve
- [x] 7.8 Run the full test suite and confirm no remaining references to `group_name`/`inventory_group` (str) remain outside of CLI-facing `--group <name>` flags (full suite green except the pre-existing infra-gated `tests/integration/test_full_backup_restore_cycle.py`, which needs a real StarRocks/S3 cluster not available in this environment)

## 8. Validation

- [x] 8.1 Run `openspec validate inventory-groups-by-id --strict` and fix any reported issues
