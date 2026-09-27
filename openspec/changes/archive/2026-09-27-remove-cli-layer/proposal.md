## Why

The project now has a fully-featured HTTP API (cluster registry, jobs, schedules, repositories, inventory groups) sitting on the same `commands/` layer the CLI uses, making the original YAML-config-driven CLI (`cli.py`) and its `starrocks-br api ...` HTTP-client subcommands (`cli_api/`) a redundant, unmaintained second surface. Keeping both means duplicated docs, duplicated tests asserting CLI/API parity, and a PyInstaller/PyPI release pipeline built around a console script the project no longer wants to be its primary interface. Removing the CLI layer leaves the HTTP API as the single, already-adequate way to operate the tool.

## What Changes

- **BREAKING**: Remove the `starrocks-br` console script entry point entirely; there is no CLI binary or command after this change.
- Delete `src/starrocks_br/cli.py` (the legacy YAML-config-driven CLI).
- Delete `src/starrocks_br/cli_api/` (the `starrocks-br api ...` HTTP-client subcommands, including `api serve`).
- Delete `src/starrocks_br/error_handler.py` (click-based CLI error formatting; used only by `cli.py`).
- Delete `entry_point.py` (PyInstaller entry point for the CLI binary).
- Delete `.github/workflows/build-executables.yml` (built and published standalone CLI binaries and the PyPI console-script package).
- Delete `src/starrocks_br/config.py` in full (YAML-config parsing: `load_config`, `validate_config`, `get_cluster_identity`, `get_table_inventory_entries`; used only by `cli.py`). This is unrelated to and does not affect `src/starrocks_br/api/config.py`, a separate, already API-owned module (`get_api_key`, `get_default_backend`, `get_enabled_backends`, `API_KEY_ENV_VAR`), which is untouched.
- Remove `bootstrap_table_inventory` from `src/starrocks_br/inventory_groups.py` (confirmed used only by `cli.py`'s `init` command).
- Delete CLI-only tests: `test_cli_backup.py`, `test_cli_restore.py`, `test_cli_init.py`, `test_cli_general.py`, `test_cli_exceptions.py`, `test_prune_cli.py`, `test_cli_api_client.py`, `test_api_cli_parity.py`, `test_error_handler.py`, `test_config.py`.
- Update `pyproject.toml`: drop `[project.scripts]`, drop the `click` dependency, and promote the `api` optional-dependency group to base `dependencies` (the API is now the only runtime mode).
- Rewrite `README.md` Installation/Basic Usage sections to describe running and using the HTTP API instead of the CLI.
- Rewrite or remove `docs/commands.md` (CLI command reference).
- Update `AGENTS.md` to drop `cli.py`/`cli_api/` from "Main source areas" and remove the CLI-specific architectural-boundary exceptions that no longer apply.
- No changes to `src/starrocks_br/commands/`, core operation modules (planner, executor, restore, prune, etc.), or the `store/` data-access layer — backup, restore, and prune functionality is unchanged and remains reachable only through the API.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `cli-api-client`: this capability is removed in full — all of its requirements (CLI authentication against the API, cluster/job/schedule management via CLI, the cron-friendly schedule-runner CLI command) no longer apply because the CLI surface they describe is deleted.
- `api-job-execution`: the requirement "Job execution reuses existing backup/restore/prune behavior unchanged" currently defines consistency in terms of "the equivalent existing CLI command" and "what the CLI command would have produced." With the CLI removed, this needs rewording to state the same guarantee (identical labels, bookkeeping, and StarRocks-side behavior across all execution backends) without referencing the CLI as a comparison point.

## Impact

- **Affected code**: `src/starrocks_br/cli.py`, `src/starrocks_br/cli_api/*`, `src/starrocks_br/error_handler.py`, `entry_point.py`, `src/starrocks_br/config.py` (deleted in full), `src/starrocks_br/inventory_groups.py` (`bootstrap_table_inventory` removed).
- **Affected tests**: 10 test files under `tests/unit/service/` covering CLI behavior, CLI/API parity, and CLI-only config parsing are removed; no other tests are expected to change since the API and commands layers already have their own coverage.
- **Affected packaging/CI**: `pyproject.toml` (entry point, dependencies), `.github/workflows/build-executables.yml` (deleted).
- **Affected docs**: `README.md`, `docs/commands.md`, `AGENTS.md`.
- **Not affected**: `src/starrocks_br/api/`, `src/starrocks_br/commands/`, `src/starrocks_br/store/`, and all core StarRocks operation modules (planner, executor, restore, prune, concurrency, history, health, repository, db, logger, timezone, utils, s3_verify) — these are shared with the API and remain fully functional.
