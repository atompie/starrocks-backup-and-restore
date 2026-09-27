## Context

See proposal.md - Why. The codebase currently has two entry points sharing the `commands/` layer: `cli.py` (direct, YAML-config-driven) and the API (`src/starrocks_br/api/`). A third piece, `cli_api/`, is an HTTP client CLI in front of the API. This change removes both CLI pieces and everything that exists solely to support them, while leaving the `commands/`, core operation modules, and `store/` layers untouched, since the API already depends on them directly and has full route coverage (clusters, jobs, schedules, repositories, inventory groups).

Two module-boundary facts drive the design-level decisions below (confirmed by import search):
- `src/starrocks_br/config.py` (top-level, YAML parsing: `load_config`, `validate_config`, `get_cluster_identity`, `get_table_inventory_entries`) is imported only by `cli.py`. It is unrelated to `src/starrocks_br/api/config.py`, a separate module the API already owns for its own settings (`get_api_key`, `get_default_backend`, `get_enabled_backends`, `API_KEY_ENV_VAR`).
- `inventory_groups.bootstrap_table_inventory` is called only from `cli.py`'s `init` command; every other function in `inventory_groups.py` is used by the API routes/commands layer.

## Goals / Non-Goals

**Goals:**
- Leave `starrocks_br.api` as the sole runtime entry point, with `commands/` and core operation modules unchanged.
- Remove every file, dependency, test, and doc section that exists only to support the CLI, without leaving dead code behind.
- Keep the change mechanically safe: delete-and-verify, not a rewrite of shared logic.

**Non-Goals:**
- Not building a CLI-replacement (e.g., a Python HTTP client library) as part of this change; a former CLI user runs the server and calls it over HTTP directly.
- Not changing any `commands/`, core operation, or `store/` behavior.
- Not migrating existing users' YAML config files - the API's cluster registry is created via API calls, not by reading legacy config files.

## Decisions

**Delete `config.py` in full, not just the YAML-specific functions.** Every function in the file is YAML/CLI-only (verified by import search: zero non-`cli.py` importers). Trimming it to "just the CLI parts" would leave an empty or near-empty file; full deletion is simpler and avoids leaving an unused module hanging around. `src/starrocks_br/api/config.py` is untouched, since it is a different file with a different job.

**Order of deletion: tests first, then source, then packaging/docs.** Delete the nine CLI-only test files and `test_config.py` first so the test suite's pass/fail signal reflects only shared code as each source file is removed - this catches any missed dependency immediately (an import error in a surviving test is a hard stop) rather than after the fact. Then delete `cli.py`, `cli_api/`, `error_handler.py`, `entry_point.py`, `config.py`, and the `bootstrap_table_inventory` function, running the full test suite after each meaningful chunk. Finally update `pyproject.toml`, `.github/workflows/build-executables.yml`, `README.md`, `docs/commands.md`, and `AGENTS.md`.

**Promote the `api` optional-dependency group to base `dependencies` in `pyproject.toml`, rather than leaving it optional.** Once the CLI (the only thing that could run without the API extras) is gone, `pip install starrocks-br` with no extras would install a package with no usable entry point at all. Making `api`'s dependencies (`fastapi`, `uvicorn`, `sqlalchemy`, `alembic`, `httpx`, `croniter`, `cryptography`, `pydantic`, `boto3`) base dependencies keeps a plain install usable.

**Drop `click` entirely rather than keeping it as an unused dependency.** After deleting `cli.py`, `cli_api/`, and `error_handler.py` (the only three importers of `click`), nothing in the codebase uses it.

**Delete `.github/workflows/build-executables.yml` rather than repurposing it.** It builds and publishes PyInstaller binaries and a PyPI console-script package around `entry_point.py`/`cli.py`; with no console script, there is nothing for it to build. If the project later wants a PyPI package for the API/library code, that would be a new, separate workflow rather than a repurposed CLI-binary builder.

**Rewrite `docs/commands.md` rather than deleting it outright**, replacing its CLI command reference with either a redirect to `docs/api.md` or removal if the material is fully redundant with `docs/api.md` after review at implementation time - deferred to a task since it depends on reading both docs side by side.

## Risks / Trade-offs

- [Risk] A currently-undiscovered import of `cli.py`, `cli_api/`, `error_handler.py`, `config.py`, or `bootstrap_table_inventory` exists outside what the import search found. → Mitigation: run the full test suite (`pytest`) after each deletion step, per the tests-first ordering above; a missed dependency surfaces as an import/collection error immediately.
- [Risk] `bootstrap_table_inventory` removal could silently drop init-time table-inventory bootstrapping if some other unnoticed path relies on it. → Mitigation: grep for the function name in the full source and test tree immediately before deleting it, not just at proposal time.
- [Risk] **BREAKING**: any existing user relying on the `starrocks-br` console script or standalone binaries loses that interface entirely with this change; there is no compatibility shim. → Mitigation: this is intentional per the proposal; call it out clearly in the README and any release notes.
- [Trade-off] Promoting `api` extras to base dependencies increases the base install's dependency footprint (adds `fastapi`, `sqlalchemy`, `alembic`, etc. unconditionally). Accepted because the API is now the only runtime mode, so these are no longer truly optional.

## Migration Plan

1. Delete CLI-only tests (`test_cli_backup.py`, `test_cli_restore.py`, `test_cli_init.py`, `test_cli_general.py`, `test_cli_exceptions.py`, `test_prune_cli.py`, `test_cli_api_client.py`, `test_api_cli_parity.py`, `test_error_handler.py`, `test_config.py`). Run the full suite - remaining failures point at code that still depends on what's about to be deleted.
2. Delete `src/starrocks_br/cli.py`, `src/starrocks_br/cli_api/`, `src/starrocks_br/error_handler.py`, `entry_point.py`, `src/starrocks_br/config.py`. Remove `bootstrap_table_inventory` from `inventory_groups.py`. Run the full suite again.
3. Update `pyproject.toml`: remove `[project.scripts]`, remove `click`, move `api` extras into base `dependencies`, remove the now-empty `api` extras group (or leave it as an empty/back-compat alias only if something external is known to reference `pip install starrocks-br[api]` - otherwise drop it).
4. Delete `.github/workflows/build-executables.yml`.
5. Update `README.md`, `docs/commands.md`, and `AGENTS.md` to remove CLI references and describe the API-only usage.
6. Run the full test suite and `openspec validate` for this change one final time before marking tasks done.

No runtime data migration is needed - this change touches only code, packaging, CI, and docs; the metadata store schema and API behavior are unaffected.
