## 1. Data model and migration

- [x] 1.1 Add nullable, indexed `group_id: int | None` column to `Job` in `src/starrocks_br/store/models.py` and verify existing model tests in `tests/unit/crud/test_store_models.py` still pass
- [x] 1.2 Generate and review an Alembic migration adding the `jobs.group_id` column and its index (no data backfill) and verify `alembic upgrade head` / `alembic downgrade -1` both run cleanly against a scratch SQLite database

## 2. Commands layer

- [x] 2.1 Update `commands/jobs.py::submit_job` to persist `group_id` on the `Job` row (for `backup_full`/`backup_incremental`, sourced the same way as the existing `params_json` value) and verify a unit test asserts the column is set after submission
- [x] 2.2 Update `commands/jobs.py::list_jobs` to accept optional `job_id` and `group_id` parameters and apply them as additional `where` clauses, and verify unit tests cover: filtering by `job_id` (match and no-match), filtering by `group_id` (match, no-match, and jobs of a type with `group_id IS NULL` are excluded)

## 3. API layer

- [x] 3.1 Add optional `job_id` and `group_id` query parameters to `GET /backup/history/cluster/{cluster_id}` in `src/starrocks_br/api/routes/jobs.py`, forwarding them to `list_jobs`, and verify a service-level test (`tests/unit/service/test_api_jobs.py`) covers both filters individually and combined with the existing `status`/`job_type`/pagination filters

## 4. Spec sync

- [x] 4.1 Confirm the delta spec in this change's `specs/api-job-execution/spec.md` matches the implemented behavior (filter semantics, exclusion of ungrouped/older jobs) before archiving
