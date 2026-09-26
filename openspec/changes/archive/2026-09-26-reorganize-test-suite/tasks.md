## 1. Scaffold new directories

- [x] 1.1 Create `tests/unit/`, `tests/unit/crud/`, `tests/unit/service/` and add
  `__init__.py` to each, matching the existing package-per-directory pattern in `tests/` and
  `tests/integration/`. Verify: `find tests/unit -name '__init__.py'` lists all three.

## 2. Move CRUD unit tests

- [x] 2.1 `git mv` these 4 files from `tests/` to `tests/unit/crud/`, unchanged: `test_inventory_groups_sql.py`, `test_history.py`, `test_store_models.py`, `test_repository_sql.py`. Verify: `git status` shows renames, no diff in content.

## 3. Move service unit tests

- [x] 3.1 `git mv` the API-layer files from `tests/` to `tests/unit/service/`: `test_api_auth.py`, `test_api_clusters.py`, `test_api_inventory_groups.py`, `test_api_jobs.py`, `test_api_repositories.py`, `test_api_repository_schemas.py`, `test_api_s3_verify.py`, `test_api_schedules.py`.
- [x] 3.2 `git mv` the CLI-layer files from `tests/` to `tests/unit/service/`: `test_cli_api_client.py`, `test_cli_backup.py`, `test_cli_exceptions.py`, `test_cli_general.py`, `test_cli_init.py`, `test_cli_restore.py`.
- [x] 3.3 `git mv` the commands-layer files from `tests/` to `tests/unit/service/`: `test_commands_backup.py`, `test_commands_clusters.py`, `test_commands_prune.py`, `test_commands_repositories.py`, `test_commands_restore.py`, `test_commands_schedules.py`.
- [x] 3.4 `git mv` the remaining core/business-logic files from `tests/` to `tests/unit/service/`: `test_backup_dispatch_parity.py`, `test_cluster_connect.py`, `test_concurrency.py`, `test_config.py`, `test_db.py`, `test_error_handler.py`, `test_exceptions.py`, `test_executor.py`, `test_health_checks.py`, `test_jobs_backend.py`, `test_jobs_handlers.py`, `test_jobs_thread_backend.py`, `test_labels.py`, `test_logger.py`, `test_planner.py`, `test_prune.py`, `test_prune_cli.py`, `test_restore.py`, `test_store_crypto.py`, `test_store_session.py`, `test_timezone.py`, `test_utils.py`.
- [x] 3.5 Verify: `tests/` root now contains only `conftest.py`, `__init__.py`, `integration/`, and `unit/` (no stray `test_*.py` files remain flat under `tests/`).

## 4. Verify test discovery and fixtures

- [x] 4.1 Run the full suite (`pytest`) and confirm all previously-passing tests still collect and pass with no changes to test logic. Fix any import path or fixture-resolution fallout caused purely by the move (e.g. relative imports within a moved test file) without weakening, skipping, or deleting any test.
- [x] 4.2 Confirm `tests/conftest.py` fixtures (`sqlite_session`, `make_cluster`, `config_file`, `invalid_yaml_file`, `setup_password_env`) remain usable from both `tests/unit/crud/` and `tests/unit/service/` without duplication, via pytest's normal conftest lookup up the directory tree.
- [x] 4.3 Confirm `tests/integration/` is unaffected and its tests still skip cleanly when the live StarRocks/MinIO stack isn't running (`pytest tests/integration/`).
- [x] 4.4 Confirm coverage reporting (`--cov=src/starrocks_br`) and the ruff `tests/**` per-file-ignore in `pyproject.toml` still apply correctly to the new nested paths.

## 5. Document the convention

- [x] 5.1 Add a "Testing conventions" section to `AGENTS.md` describing the `tests/integration/` vs. `tests/unit/crud/` vs. `tests/unit/service/` split and the criteria for each (real infra + no mocking; simple CRUD + mocked; business/application logic + mocked), so future tests are placed consistently. Verify: section renders correctly and matches the actual resulting structure.
