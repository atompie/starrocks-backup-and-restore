## Why

The Alembic migration history has grown to 6 chained revisions (`9bf7a7f90f78` initial schema through `fcb00bbb052c` add job group id) that only ever ran against pre-production databases — every prior schema-changing proposal (`move-ops-tables-to-sqlite`, `inventory-groups-by-id`, `decouple-database-and-repository-from-cluster`) explicitly notes there is no production data to preserve. Carrying that intermediate history (tables created then dropped, columns added then removed) adds no value and only obscures what the schema actually looks like today. Collapsing it into a single baseline migration that matches `store/models.py` as it stands now makes the schema legible and keeps future migrations linear from a clean starting point.

## What Changes

- **BREAKING**: Replace the 6 existing Alembic revisions under `src/starrocks_br/store/migrations/versions/` with a single new baseline revision (`down_revision = None`) that creates the current 9 tables (`clusters`, `jobs`, `schedules`, `inventory_groups`, `table_inventory`, `backup_history`, `restore_history`, `run_status`, `backup_partitions`) exactly as defined in `store/models.py` today.
- Delete the 6 retired migration files and their committed `__pycache__` artifacts.
- Any existing local/dev SQLite database created against the old revision chain is no longer upgradable in place; it must be deleted and re-initialized with `alembic upgrade head` against the new baseline (acceptable per user direction, since no production data exists on the old chain).
- No changes to `store/models.py`, application code, or API behavior — this is a pure refactor of migration history, not a schema or behavior change.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
None — no spec-level behavior changes. This change only restructures Alembic migration history to match already-current, already-specified schema/behavior. `skip_specs: true` is set in `.openspec.yaml` accordingly.

## Impact

- **Affected code**: `src/starrocks_br/store/migrations/versions/*.py` (6 files deleted, 1 new file added), their `__pycache__` counterparts (deleted).
- **Not affected**: `src/starrocks_br/store/models.py`, `src/starrocks_br/store/session.py`, application/API/commands code, `openspec/specs/*` (no active spec references the removed intermediate schema states — only archived, historical change proposals do, and those remain untouched as historical record).
- **Affected users**: any developer with a local SQLite metastore created before this change must delete it and re-run `alembic upgrade head`.
- **Dependencies**: none new; same SQLAlchemy/Alembic stack already in place.
