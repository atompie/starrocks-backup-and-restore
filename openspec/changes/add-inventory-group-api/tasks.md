## 1. Shared connection helper (deduplication)

- [x] 1.1 Create `src/starrocks_br/api/routes/_cluster_connect.py` with `get_cluster_or_404`,
      `connect`, `connect_or_503`, moving the existing bodies verbatim from
      `repositories.py`'s `_connect`/`_connect_or_503` (lines ~45-69) and the `_get_cluster_or_404`
      duplicated in `repositories.py`, `jobs.py:29-33`, and `clusters.py`.
- [x] 1.2 Update `repositories.py`, `jobs.py`, and `clusters.py` to import from
      `_cluster_connect.py` instead of defining their own copies, and verify
      `pytest tests/test_api_clusters.py tests/test_api_repositories.py tests/test_api_jobs.py`
      passes unchanged before proceeding. (The `FakeDB`-based connect patch target in
      `test_api_repositories.py` moved from `repositories._connect` to
      `_cluster_connect.connect`, same observable behavior.)

## 2. Domain module

- [x] 2.1 Create `src/starrocks_br/inventory_groups.py` with
      `InventoryGroupNotFoundError`, `InventoryMembershipConflictError`,
      `InventoryMembershipNotFoundError` exception classes.
- [x] 2.2 Implement `list_groups`, `group_exists`, `get_group` using
      `utils.quote_value` for all interpolated values, verified by unit
      tests in task 6.1. (`ops_database` is interpolated unquoted, matching the existing
      convention in `planner.py`/`prune.py`/`concurrency.py` - it is a trusted config value, not
      per-request user input.)
- [x] 2.3 Implement `add_membership` and `add_memberships_bulk` (loops `add_membership`, matching
      `bootstrap_table_inventory`'s partial-success-is-acceptable idempotency). Deviation from the
      original plan: `table_inventory` is a StarRocks UNIQUE KEY table, which upserts (replaces)
      on a duplicate key instead of raising an insert error, so `add_membership` detects a
      conflict with a `SELECT`-based existence check before inserting, not by catching an insert
      error.
- [x] 2.4 Implement `remove_membership` (raising `InventoryMembershipNotFoundError` if absent) and
      `delete_group` (raising `InventoryGroupNotFoundError` if the group has zero rows, returning
      the deleted row count from a preceding `COUNT(*)`).

## 3. API schemas

- [x] 3.1 Add `InventoryGroupSummary`, `InventoryMembershipCreate`, `InventoryGroupCreate`,
      `InventoryMembershipRead`, `InventoryGroupRead` to `src/starrocks_br/api/schemas.py`,
      following the `RepositoryCreate`/`RepositoryRead` plain-dict-serialization pattern.

## 4. API router

- [x] 4.1 Create `src/starrocks_br/api/routes/inventory_groups.py` with the six endpoints (list,
      create, get, add-table, remove-table, delete) under
      `/clusters/{cluster_id}/inventory-groups`, using `get_cluster_or_404`/`connect_or_503` from
      `_cluster_connect.py` and translating `InventoryGroupNotFoundError` → 404,
      `InventoryMembershipConflictError` → 409, `InventoryMembershipNotFoundError` → 404.
- [x] 4.2 Register the router in `src/starrocks_br/api/app.py` and verify `/docs` shows the new
      `inventory-groups` tag with all six endpoints. (Verified via `/openapi.json` through
      `TestClient` - all 4 distinct paths, 6 operations, present.)

## 5. Fail-fast group validation and hardening

- [x] 5.1 Add a `_submit_backup_job` helper in `jobs.py` that calls
      `inventory_groups.group_exists` before `submit_job` for `backup_full`/`backup_incremental`,
      raising `HTTPException(404, ...)` when the group is missing or unknown; route
      `submit_backup_full`/`submit_backup_incremental` through it.
- [x] 5.2 In `schema.py`, fix `bootstrap_table_inventory` (lines ~93-122) to remove raw f-string
      interpolation of `group`/`database`/`table` and the `SHOW DATABASES LIKE '{database_name}'`
      calls, delegating row insertion to `inventory_groups.add_membership` and treating
      `InventoryMembershipConflictError` as a no-op.
- [x] 5.3 In `jobs/handlers.py`, change `run_backup_full`/`run_backup_incremental`'s
      `group = params["group"]` to `params.get("group")` plus an explicit `ValueError` when absent.

## 6. Tests

- [x] 6.1 Create `tests/test_inventory_groups_sql.py` covering `list_groups` grouping/counting,
      `get_group` dict shape, `add_membership` conflict handling, `remove_membership`/
      `delete_group` not-found handling, and assertions that all SQL is built via
      `quote_value`. 15 tests, all passing.
- [x] 6.2 Create `tests/test_api_inventory_groups.py` with the 13+ scenarios listed in
      TASK_1.md (list/create/get/add/remove/delete success and error paths, unknown cluster,
      unreachable cluster, plus a 422 for an empty `tables` list), following
      `test_api_repositories.py`'s fake-DB pattern. 14 tests, all passing.
- [x] 6.3 Add `test_submit_backup_full_unknown_group_is_404`,
      `test_submit_backup_incremental_unknown_group_is_404`, and
      `test_submit_backup_full_missing_group_is_404` to `tests/test_api_jobs.py`, and update
      existing happy-path backup tests (and `test_delete_cluster_blocked_by_active_job` in
      `test_api_clusters.py`, which also submits a backup) to monkeypatch `group_exists` to `True`
      and stub the cluster connection so tests don't attempt a real socket connection.
- [x] 6.4 Update `tests/test_jobs_handlers.py` to assert `run_backup_full`/`run_backup_incremental`
      raise `ValueError` (not `KeyError`) when `group` is absent.
- [x] 6.5 Extend `tests/test_schema_setup.py` (the actual file covering `schema.py`; no
      `tests/test_schema.py` exists in this repo) asserting a group name containing a single quote
      (`o'brien_group`) is safely inserted via `bootstrap_table_inventory` without breaking the SQL
      string. Also updated several pre-existing `bootstrap_table_inventory` tests whose mocked
      `db.query` side effects were exhausted by the new existence-check query that `add_membership`
      issues per entry.
- [x] 6.6 Run the full verification suite:
      `pytest tests/test_api_clusters.py tests/test_api_repositories.py tests/test_api_jobs.py tests/test_jobs_handlers.py tests/test_inventory_groups_sql.py tests/test_api_inventory_groups.py tests/test_schema_setup.py`
      - all 113 tests pass. Also ran the full repo suite and confirmed the only failures
      (`test_cli_backup.py`, `test_cli_exceptions.py`, `test_cli_init.py`, `test_executor.py`) are
      pre-existing, unrelated to this change (reproduced on `main` before these edits).

## 7. Manual verification

- [x] 7.1 Verified via `TestClient` against the real router/schema stack (no live StarRocks
      cluster available in this environment): the `inventory-groups` endpoints are registered and
      reachable, `POST .../inventory-groups` on a valid payload returns 201 through the
      `create_inventory_group` route with mocked `StarRocksDB` calls (test suite), and `GET
      .../inventory-groups` reaches `list_groups` and returns its shape.
- [x] 7.2 Verified via `TestClient`: `POST /clusters/{id}/backups/full` with an unreachable/unknown
      cluster fails fast (503 for connection failure, or 404 for unknown group when connected) -
      confirmed by `test_submit_backup_full_unknown_group_is_404`,
      `test_submit_backup_full_missing_group_is_404`, and the existing happy-path 202 tests. Live
      end-to-end verification against a real StarRocks cluster was not performed (none available in
      this environment).
