## 1. Manual Backups (jobs.py)

- [x] 1.1 Rename `jobs.router` tags to `["manual-backups"]` and update the four submission routes to `/backup/manual/full/cluster/{cluster_id}`, `/backup/manual/incremental/cluster/{cluster_id}`, `/backup/manual/restore/cluster/{cluster_id}`, `/backup/manual/prune/cluster/{cluster_id}`, leaving `GET /job/{job_id}` untouched; verify by starting the app and checking `/openapi.json` lists the new paths under the renamed tag. Also updated `cli_api/job.py`'s `_ENDPOINT_BY_TYPE` map and submit-request path (not explicitly named in this task, but the same call site).
- [x] 1.2 Update `tests/test_api_jobs.py` to call the new paths; verify with `pytest tests/test_api_jobs.py`.
- [x] 1.3 Update `tests/integration/test_full_backup_restore_cycle.py` for the new backup/restore/prune submission paths (job-status calls stay as-is); verify with `pytest tests/integration/test_full_backup_restore_cycle.py`. (Also fixed its repositories/inventory-groups path references, discovered while editing the same file; not run — this test hits a real StarRocks cluster.)

## 2. Backup Schedules (schedules.py)

- [x] 2.1 Update `create_schedule`/`list_schedules` to `/backup/schedules/cluster/{cluster_id}` and `get_schedule`/`update_schedule`/`delete_schedule` to `/backup/schedules/cluster/{cluster_id}/schedule_id/{schedule_id}`; verify by checking `/openapi.json`.
- [x] 2.2 Update `run_due` to `POST /backup/schedules/run`; verify by checking `/openapi.json`.
- [x] 2.3 Update `tests/test_api_schedules.py` and `tests/test_commands_schedules.py` (if it references HTTP paths) to the new paths; verify with `pytest tests/test_api_schedules.py tests/test_commands_schedules.py`. (`test_commands_schedules.py` had no HTTP path references; nothing to change there. Also fixed a schedule-creation path in `tests/test_api_inventory_groups.py`, discovered while running its suite.)
- [x] 2.4 Update `src/starrocks_br/cli_api/schedule.py` (`create`, `list`, `delete`, `run-due` call sites) to the new paths; verify with `pytest tests/test_cli_api_client.py -k schedule`.

## 3. Repositories (repositories.py)

- [x] 3.1 Update `list_repositories`/`create_repository` to `/repositories/cluster/{cluster_id}` and `delete_repository` to `/repositories/cluster/{cluster_id}/name/{name}`, leaving `POST /repositories/verify` untouched; verify by checking `/openapi.json`.
- [x] 3.2 Update `tests/test_api_repositories.py` and `tests/test_api_s3_verify.py` (if it calls cluster-scoped repository paths) to the new paths; verify with `pytest tests/test_api_repositories.py tests/test_api_s3_verify.py`. (`test_api_s3_verify.py` had no cluster-scoped repository paths; nothing to change there.)
- [x] 3.3 Update `src/starrocks_br/cli_api/repository.py` (`add`, `list`, `remove` call sites) to the new paths; verify with `pytest tests/test_cli_api_client.py -k repository`.

## 4. Inventory (inventory_groups.py)

- [x] 4.1 Update `list_inventory_groups`/`create_inventory_group` to `/inventories/cluster/{cluster_id}`; verify by checking `/openapi.json`.
- [x] 4.2 Update `get_inventory_group`, `add_inventory_group_table`, `remove_inventory_group_table`, `delete_inventory_group` to `/inventory/cluster/{cluster_id}/group_id/{group_id}` (and its `/tables`, `/tables/{database_name}/{table_name}` sub-paths); verify by checking `/openapi.json`.
- [x] 4.3 Update `tests/test_api_inventory_groups.py` to the new paths; verify with `pytest tests/test_api_inventory_groups.py`.
- [x] 4.4 Update `src/starrocks_br/cli_api/client.py` (the inventory-groups listing call used for name-to-id resolution) to the new path; verify with `pytest tests/test_cli_api_client.py`. (Also fixed a stale mock-handler path match in `tests/test_cli_api_client.py::test_job_submit_with_wait_polls_until_terminal_and_fails_on_failure`, discovered when this suite ran red.)

## 5. Documentation

- [x] 5.1 Update `docs/api.md`: rename the "Jobs" section to "Manual Backups" and update its table/examples; update the "Schedules" (renamed "Backup Schedules") and "Repositories" sections' paths; add the missing "Inventory Groups" section documenting the new `/inventories/...` and `/inventory/...` endpoints; update the CLI Reference table's endpoint column.
- [x] 5.2 Update `docs/commands.md` and `docs/scheduling.md` for any literal path references found by grep. (`docs/commands.md` had literal `curl` examples that needed updating; `docs/scheduling.md` had none.)

## 6. Full verification

- [x] 6.1 Run the full test suite (`pytest`) and confirm it passes. All pass except `tests/integration/test_full_backup_restore_cycle.py::test_full_backup_then_restore_recovers_dropped_database`, which fails identically on `main` before this change (confirmed via `git stash`) — it needs a live StarRocks cluster and a local MinIO/S3 mock on `127.0.0.1:9000`, neither running in this environment; unrelated to the path rename. While fixing the rest, also updated stray old-path references this task list didn't name: `src/starrocks_br/cli_api/job.py` (`_ENDPOINT_BY_TYPE` + submit path), a schedule-creation path and an inventory-group-listing helper in `tests/test_api_schedules.py`, an inventory-group helper in `tests/test_api_inventory_groups.py`, a backup-submission path in `tests/test_api_clusters.py`, and a mock-handler path match in `tests/test_cli_api_client.py`.
- [x] 6.2 Grep the repo (excluding `openspec/changes/archive/`) for every old path fragment (`/cluster/{cluster_id}/backups`, `/cluster/{cluster_id}/restores`, `/cluster/{cluster_id}/prunes`, `/cluster/{cluster_id}/schedules`, `/cluster/{cluster_id}/schedule/`, `/schedules/run-due`, `/cluster/{cluster_id}/repositories`, `/cluster/{cluster_id}/inventory-groups`) and confirm zero remaining references outside the archive. Zero found.
- [x] 6.3 Start the app and fetch `/openapi.json`; confirm every path listed matches the design.md table exactly, with no leftover old paths. Confirmed — output matches design.md's table exactly (plus unchanged `/cluster`, `/clusters`, `/health`, `/job/{job_id}`, `/repositories/verify`).
