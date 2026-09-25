## 1. Schemas

- [x] 1.1 In `src/starrocks_br/api/schemas.py`, add `model_validator` to the `pydantic` import and
      add `BackupFullRequest`, `BackupIncrementalRequest`, `RestoreRequest`, `PruneRequest` (each
      `extra="forbid"`, fields per proposal.md - Impact / TASK_2.md's field list), with
      `RestoreRequest._check_group_and_table_not_both_set` and
      `PruneRequest._check_exactly_one_strategy` validators. Verify with
      `python -c "import starrocks_br.api.schemas"`.
- [x] 1.2 Delete the `JobSubmitRequest` class from the same file. Verify with
      `grep -rn JobSubmitRequest src/` returning no matches.

## 2. Routes

- [x] 2.1 In `src/starrocks_br/api/routes/jobs.py`, update the import to pull in the four new
      models instead of `JobSubmitRequest`.
- [x] 2.2 Update `_submit` to type `payload: BaseModel` and drop `exclude_none=True` from
      `payload.model_dump(exclude={"backend"})`.
- [x] 2.3 Update `_submit_backup_job` to type `payload: BackupFullRequest | BackupIncrementalRequest`,
      remove the `if not payload.group: raise HTTPException(404, ...)` branch (now unreachable —
      Pydantic rejects a missing/empty `group` before the route runs), and drop `exclude_none=True`
      from its `model_dump` call, keeping the `inventory_groups.group_exists` 404 check.
- [x] 2.4 Update `submit_backup_full`/`submit_backup_incremental`/`submit_restore`/`submit_prune`
      signatures to take `BackupFullRequest`/`BackupIncrementalRequest`/`RestoreRequest`/`PruneRequest`
      respectively. Verify the app still imports: `python -c "from starrocks_br.api.app import app"`
      (or equivalent app entrypoint).

## 3. Tests

- [x] 3.1 In `tests/test_api_jobs.py`, monkeypatch `inventory_groups.group_exists` to `True` for
      existing backup_full/backup_incremental valid-payload tests (needed now that group-existence
      runs before job creation via the still-present check).
- [x] 3.2 Add `test_submit_backup_full_missing_group_is_422` (POST `{}` → 422).
- [x] 3.3 Add `test_submit_backup_full_rejects_foreign_field_is_422`
      (POST `{"group": "g1", "keep_last": 5}` → 422).
- [x] 3.4 Add `test_submit_restore_both_group_and_table_is_422`.
- [x] 3.5 Add `test_submit_restore_neither_group_nor_table_succeeds` (confirms restoring
      "everything in the backup" still returns 202, not 422).
- [x] 3.6 Add `test_submit_prune_no_strategy_is_422`.
- [x] 3.7 Add `test_submit_prune_two_strategies_is_422`.
- [x] 3.8 Add `test_submit_prune_extra_field_is_422` (e.g. POST `{"snapshot": "x", "table": "t"}` → 422).
- [x] 3.9 Run `pytest tests/test_api_jobs.py tests/test_jobs_handlers.py` and verify all tests pass,
      confirming `tests/test_jobs_handlers.py` needed no changes.

## 4. Manual verification

- [x] 4.1 Start the API and check `/docs`: confirm `/backups/full`'s request schema lists only
      `group`, `name`, `backend`, and `/prunes`'s lists only `group`, `keep_last`, `older_than`,
      `snapshot`, `snapshots`, `dry_run`, `backend`.
- [x] 4.2 POST `{}` to `/backups/full` → 422 with a clear "field required" message for `group`.
- [x] 4.3 POST `{"target_label": "x", "group": "g1", "table": "t1"}` to `/restores` → 422
      ("Cannot specify both...").
