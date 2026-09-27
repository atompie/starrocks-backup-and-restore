## Context

`src/starrocks_br/store/migrations/versions/` currently holds 6 chained Alembic revisions (`9bf7a7f90f78` → `059d2b79d525` → `ff654976aa0e` → `8c4f5dcf3c2c` → `d96bf30a5582` → `fcb00bbb052c`), each auto-generated with `sa.Column`/`op.create_table` calls in the same style (see `9bf7a7f90f78_initial_schema.py`). `store/models.py` (210 lines) is the current, already-correct schema: 9 tables — `clusters`, `jobs`, `schedules`, `inventory_groups`, `table_inventory`, `backup_history`, `restore_history`, `run_status`, `backup_partitions` — with the FKs, `UniqueConstraint`s, and `Index`es declared in each model's `__table_args__`/`mapped_column`. See proposal.md for why this history is being collapsed.

## Goals / Non-Goals

**Goals:**
- Produce one new Alembic revision, `down_revision = None`, whose `upgrade()` creates exactly the 9 tables as `store/models.py` defines them today (same columns, types, nullability, defaults-at-DB-level where Alembic tracks them, FKs with their `ondelete` behavior, unique constraints, and indexes).
- Remove the 6 retired revision files and their committed `__pycache__/*.pyc` companions.
- Keep the new file's style consistent with the existing auto-generated migrations (`sa.Column(...)`, `op.create_table(...)`, `op.f(...)` index naming) so it doesn't stand out as hand-written.

**Non-Goals:**
- No change to `store/models.py`, `store/session.py`, or any application/API/commands code.
- No upgrade path from a database already stamped at the old chain's head (`fcb00bbb052c`) — per user direction, a pre-existing local dev DB is deleted and re-initialized against the new baseline rather than migrated in place.
- No change to any file under `openspec/specs/` — confirmed no active spec references the removed intermediate schema states.

## Decisions

- **Single hand-assembled revision vs. re-running `alembic revision --autogenerate` against a fresh DB**: use autogenerate against an empty target database (SQLite scratch file) driven by the current `Base.metadata`, then review the generated `upgrade()`/`downgrade()` before committing. This guarantees the table definitions match `models.py` byte-for-byte (correct column order, types, constraint names) rather than risking a manual transcription error, while still producing output in the same auto-generated style as today's files.
- **New revision id and message, not reusing `9bf7a7f90f78`/"initial schema"**: per user direction, use a fresh revision id (whatever `alembic revision` generates) with a message like `"baseline schema"`, to signal this is a deliberate reset of history rather than a continuation of the original initial-schema revision.
- **`downgrade()` drops all 9 tables** (mirroring the pattern in the retired `9bf7a7f90f78` file: drop indexes then tables, reverse creation order) rather than being a no-op — keeps symmetry with existing migration conventions even though there is no revision below it to downgrade to.
- **No data-preservation or dual-write logic**: consistent with every prior schema-changing proposal in this repo (`move-ops-tables-to-sqlite`, `inventory-groups-by-id`, `decouple-database-and-repository-from-cluster`), which each stated no production data exists. This change makes the same assumption explicit as its central premise rather than a side note.

## Risks / Trade-offs

- **[Risk]** A developer or CI environment has a SQLite file already stamped at `fcb00bbb052c` (or any prior revision). Running `alembic upgrade head` against it after this change either does nothing useful (Alembic sees an unrecognized revision in `alembic_version` and errors) or, if manually stamped, silently skips re-creating tables it thinks already exist. → **Mitigation**: call this out prominently in the proposal's Impact section and in the new revision's docstring; document in the change's task list that any existing dev/test SQLite file must be deleted before running `alembic upgrade head` against the new baseline.
- **[Risk]** Autogenerate against a fresh SQLite scratch DB could miss a constraint that only matters on MySQL/Postgres (the models docstring notes types are chosen to be portable across backends), or Alembic's SQLite-specific rendering (e.g., batch mode for constraints) could differ from what a Postgres-target generation would produce. → **Mitigation**: after generating, manually cross-check every column/type/constraint in the new revision against `models.py` line by line rather than trusting autogenerate output blindly; this is the same manual-review step every prior migration in this repo already required.

## Migration Plan

1. Generate the new revision with `alembic revision --autogenerate -m "baseline schema"` against a fresh, empty SQLite database (no prior `alembic_version` stamp), using the current `env.py`/`Base.metadata`.
2. Manually review the generated `upgrade()` against `store/models.py` for completeness (all 9 tables, all FKs incl. `ondelete`, all `UniqueConstraint`/`Index` names) and adjust `downgrade()` to drop indexes then tables in reverse creation order, matching existing file style.
3. Delete the 6 retired migration files and their `__pycache__/*.pyc` files.
4. Verify: `rm` any local scratch SQLite file, then `alembic upgrade head` succeeds and `sqlite3 <file> .schema` shows exactly the 9 expected tables; `alembic downgrade base` then cleanly drops them all.
5. Rollback: since this only touches migration files (not `models.py` or runtime code), rollback is a plain `git revert` of this change's commit — no data-preservation concern, matching the precedent set by `move-ops-tables-to-sqlite`'s own migration plan.
