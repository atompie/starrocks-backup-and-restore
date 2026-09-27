## 1. Generate the baseline revision

- [x] 1.1 Point `STARROCKS_BR_DATABASE_URL` at a fresh, empty scratch SQLite file (no `alembic_version` table) and run `alembic revision --autogenerate -m "baseline schema"`, and verify a new file appears under `src/starrocks_br/store/migrations/versions/` with `down_revision = None`.
- [x] 1.2 Review the generated `upgrade()` line by line against `src/starrocks_br/store/models.py` and confirm all 9 tables (`clusters`, `jobs`, `schedules`, `inventory_groups`, `table_inventory`, `backup_history`, `restore_history`, `run_status`, `backup_partitions`) are created with matching columns, types, nullability, FKs (including `ondelete="CASCADE"`/`"RESTRICT"` where declared), `UniqueConstraint`s, and `Index`es, adjusting the file by hand for any mismatch or omission.
- [x] 1.3 Rewrite `downgrade()` to drop all created indexes then all 9 tables in reverse creation order, matching the style of the retired `9bf7a7f90f78_initial_schema.py`, and verify `alembic downgrade base` runs cleanly against the scratch database.

## 2. Remove retired migration history

- [x] 2.1 Delete the 6 retired migration files (`9bf7a7f90f78_initial_schema.py`, `059d2b79d525_move_ops_tables_into_sqlite.py`, `ff654976aa0e_inventory_groups_by_id.py`, `8c4f5dcf3c2c_drop_cluster_database_and_repository.py`, `d96bf30a5582_add_schedule_repository.py`, `fcb00bbb052c_add_job_group_id.py`) and verify `ls src/starrocks_br/store/migrations/versions/*.py` shows only the new baseline file.
- [x] 2.2 Delete the `__pycache__` directory under `src/starrocks_br/store/migrations/versions/` (and any other committed `.pyc` files under `src/starrocks_br/store/migrations/`), and verify `git status` shows them removed.

## 3. Verify end to end

- [x] 3.1 Against a freshly deleted/empty SQLite file, run `alembic upgrade head` and verify it succeeds with exactly one entry in `alembic_version`, then inspect `.schema` and confirm the 9 expected tables and no others are present.
- [x] 3.2 Run the full test suite (`pytest tests/`) and verify it passes unchanged, confirming this is a pure migration-history refactor with no behavior change.
- [x] 3.3 Update `README`/CHANGELOG or any developer-facing note that mentions the old migration chain or instructs running `alembic upgrade head` from scratch, adding a line that any pre-existing local/dev SQLite metastore must be deleted and re-initialized against the new baseline revision.
