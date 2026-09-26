## 1. API routes

- [x] 1.1 In `src/starrocks_br/api/schemas.py`, remove `cluster_id` from `ScheduleCreate` (verify: `ScheduleCreate` no longer has a `cluster_id` field; `ScheduleRead` keeps it as a response field).
- [x] 1.2 In `src/starrocks_br/api/routes/schedules.py`, replace the `schedule_router`/`schedules_router` prefix pair with a single `APIRouter` declaring full paths per route: `POST /cluster/{cluster_id}/schedules`, `GET /cluster/{cluster_id}/schedules`, `GET /cluster/{cluster_id}/schedule/{schedule_id}`, `PATCH /cluster/{cluster_id}/schedule/{schedule_id}`, `DELETE /cluster/{cluster_id}/schedule/{schedule_id}`, and unchanged `POST /schedules/run-due` (verify: `openapi.json` from the running app lists exactly these five cluster-scoped paths plus `run-due`, no bare `/schedule` or `/schedules` list path remains).
- [x] 1.3 Wire each route to take `cluster_id: int` as a path parameter, call `get_cluster_or_404` from `._cluster_connect` before touching schedules, and build/filter `Schedule` rows using that `cluster_id` (verify: creating/getting/listing/updating/deleting against an unregistered `cluster_id` returns 404 before any schedule-specific logic runs).
- [x] 1.4 Update `_get_schedule_or_404` to take and filter on `cluster_id` alongside `schedule_id` (`WHERE id = :schedule_id AND cluster_id = :cluster_id`) so a schedule fetched via the wrong cluster's path 404s (verify: a schedule created under cluster A returns 404 when fetched/updated/deleted via cluster B's path with the same `schedule_id`).
- [x] 1.5 Update `src/starrocks_br/api/app.py` router registration to match the new router split from 1.2 (verify: app starts and `create_app()` includes the schedules router).

## 2. CLI

- [x] 2.1 In `src/starrocks_br/cli_api/schedule.py`, change `schedule_add` to POST to `/cluster/{cluster_id}/schedules` and drop `cluster_id` from the JSON body (verify: `starrocks-br api schedule add --cluster <id> ...` hits the new path).
- [x] 2.2 Change `schedule_list` to require a `--cluster <cluster_id>` option and GET `/cluster/{cluster_id}/schedules` instead of `/schedules` (verify: `starrocks-br api schedule list --cluster <id>` only prints schedules for that cluster).
- [x] 2.3 Change `schedule_remove` to DELETE `/cluster/{cluster_id}/schedule/{schedule_id}`, adding a `--cluster <cluster_id>` option (verify: `starrocks-br api schedule remove --cluster <id> <schedule_id>` hits the new path).
- [x] 2.4 Leave `schedule_run_due` untouched (`POST /schedules/run-due` stays global) (verify: `starrocks-br api schedule run-due` still works with no cluster option).

## 3. Tests

- [x] 3.1 Update `tests/test_api_schedules.py` to call the new `/cluster/{cluster_id}/schedules[...]` paths, and add cases for: cross-cluster 404 on get/update/delete, and list scoped to one cluster only (verify: `pytest tests/test_api_schedules.py` passes).
- [x] 3.2 Update `tests/test_cli_api_client.py` schedule-related cases to pass `--cluster` and assert the new URLs (verify: `pytest tests/test_cli_api_client.py` passes). No changes needed - the only schedule cases in this file exercise `run-due`, whose route/CLI path is unchanged.
- [x] 3.3 Check `tests/test_commands_schedules.py` and `tests/test_commands_clusters.py` for any schedule-route URL assumptions and update as needed (verify: both files pass under `pytest`). No changes needed - both test the command layer (`run_due_schedules`) directly, not HTTP routes.
- [x] 3.4 Run the full test suite (verify: `pytest` passes with no regressions).

## 4. Docs

- [x] 4.1 Update any README/API reference documenting the old flat schedule endpoints to the new `/cluster/{cluster_id}/...` paths (verify: `grep -rn '"/schedule"' --include=*.md .` and `grep -rn '"/schedules"' --include=*.md .` outside `openspec/` find no stale references). Updated `docs/api.md`, `docs/commands.md`, `docs/scheduling.md`.
