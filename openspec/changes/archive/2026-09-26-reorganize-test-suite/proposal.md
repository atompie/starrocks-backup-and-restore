## Why

The test suite currently mixes real-infrastructure and mocked tests, and the mocked tests mix
simple CRUD checks with complex business-logic checks, all as ~40 flat files directly under
`tests/`. There is no folder-level signal for what a test requires (a live StarRocks/S3 stack vs.
nothing) or what kind of behavior it verifies (a single data-access operation vs. multi-step
application logic), which makes it harder to run the right subset of tests and to place new tests
consistently.

## What Changes

- Move all tests that connect to real external infrastructure (StarRocks, S3) into
  `tests/integration/`. `tests/integration/` already exists and already holds exactly this kind
  of test (`test_full_backup_restore_cycle.py`, `test_repositories_live.py`); no test files need
  to move into it, since inspection confirmed the 40 flat files under `tests/` all mock their
  external dependencies.
- Move the 40 mocked test files currently flat under `tests/` into `tests/unit/`, split further
  into:
  - `tests/unit/crud/`: simple create/read/update/delete tests on a single store/data-access
    operation (`test_inventory_groups_sql.py`, `test_history.py`, `test_store_models.py`,
    `test_repository_sql.py`).
  - `tests/unit/service/`: tests of business/application logic - multi-step workflows,
    coordination, validation, dispatch, concurrency, encryption, error handling (the remaining 36
    files).
- Add `tests/unit/__init__.py`, `tests/unit/crud/__init__.py`, `tests/unit/service/__init__.py` to
  match the existing package-per-directory pattern used by `tests/` and `tests/integration/`.
- Keep `tests/conftest.py` (shared fixtures: `sqlite_session`, `make_cluster`, `config_file`, etc.)
  at the `tests/` root so both `unit/crud/` and `unit/service/` inherit it via pytest's normal
  conftest lookup.
- Add a "Testing conventions" section to `AGENTS.md` documenting the `integration` vs.
  `unit/crud` vs. `unit/service` split so new tests are placed consistently.
- No production code changes are anticipated. `pyproject.toml`'s `testpaths = ["tests"]` and the
  `tests/**` ruff per-file-ignore glob already recurse into subdirectories, so no config changes
  are expected; this will be confirmed by running the full suite after the move.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None - this is a pure test-organization change with no change to application or API behavior.

## Impact

- Affected paths: `tests/**` (file moves only, no test logic changes), `AGENTS.md` (new
  conventions section).
- No changes to `src/starrocks_br/**`.
- No changes to public CLI/API behavior.
- Test discovery and CI: verified by running the full suite after the move; fix any import or
  fixture-path fallout without weakening, skipping, or deleting tests.
