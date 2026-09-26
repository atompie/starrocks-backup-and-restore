## Why

Schedule routes are the only resource in the API that isn't nested under `/cluster/{cluster_id}/...`: they're flat (`/schedule/{schedule_id}`, `/schedules`) with `cluster_id` carried in the request body instead of the URL path. Jobs and inventory-groups already follow the `/cluster/{cluster_id}/...` convention. Aligning schedules with that convention makes `cluster_id` a first-class part of the schedule's identity in the URL (as it already is in the data model via the `Schedule.cluster_id` foreign key), and removes the one inconsistent surface in an otherwise uniform API.

## What Changes

- Nest schedule CRUD routes under `/cluster/{cluster_id}/...`, matching the existing `jobs`/`inventory-groups` pattern:
  - `POST /schedule` -> `POST /cluster/{cluster_id}/schedules`
  - `GET /schedules` (all clusters) -> `GET /cluster/{cluster_id}/schedules` (**BREAKING**: drops the cross-cluster list; listing is now cluster-scoped only, same as jobs and inventory-groups)
  - `GET /schedule/{schedule_id}` -> `GET /cluster/{cluster_id}/schedule/{schedule_id}`
  - `PATCH /schedule/{schedule_id}` -> `PATCH /cluster/{cluster_id}/schedule/{schedule_id}`
  - `DELETE /schedule/{schedule_id}` -> `DELETE /cluster/{cluster_id}/schedule/{schedule_id}`
- `cluster_id` moves from the `ScheduleCreate` request body into the URL path; `Schedule.cluster_id` is set from the path parameter, not from the payload.
- Path `cluster_id` is validated against the registered cluster (404 if missing), reusing the existing `get_cluster_or_404` helper - same as jobs and inventory-groups.
- Fetching, updating, or deleting a schedule under the wrong cluster's path (schedule exists, but belongs to a different `cluster_id`) returns 404, not the schedule.
- `POST /schedules/run-due` is unchanged: it stays a global, non-cluster-scoped trigger endpoint, since it's a cross-cluster batch operation, not a lookup of one cluster's schedules.
- Update the CLI (`starrocks-br api schedule add/list/remove`) to build URLs under the target cluster, and `schedule list` to require/accept a `--cluster` option instead of listing across all clusters.
- `group_name` stays a plain string scoped by `(cluster_id, group_name)`, unchanged - it's consistent with every other group-referencing feature (backup/restore/prune jobs, inventory-groups, planner), none of which use a group id. There is no `Group` entity/id in the codebase to reference.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `api-scheduling`: schedule creation, retrieval, listing, update, and deletion are now scoped under a cluster path parameter (`/cluster/{cluster_id}/...`) instead of being flat, top-level routes with `cluster_id` in the body; listing schedules is now per-cluster instead of cross-cluster.
- `cli-api-client`: the schedule management commands (`add`, `list`, `remove`) now target a specific cluster via the CLI, instead of `list` returning schedules for all clusters.

## Impact

- **Code**: `src/starrocks_br/api/routes/schedules.py` (route paths, path-param wiring), `src/starrocks_br/api/schemas.py` (`ScheduleCreate` drops `cluster_id`), `src/starrocks_br/cli_api/schedule.py` (URL building, `list` command gains cluster scoping), `src/starrocks_br/api/app.py` (router registration, if the router split changes).
- **API clients**: any existing external caller of `POST /schedule`, `GET /schedules`, `GET|PATCH|DELETE /schedule/{schedule_id}` must switch to the new `/cluster/{cluster_id}/...` paths - **BREAKING** change to the schedule API surface. No deprecation period is in scope for this change.
- **Tests**: existing schedule route/CLI tests need their URLs and fixtures updated to the new nested paths.
- **Docs**: any README/API reference documenting the old schedule endpoints needs updating.
