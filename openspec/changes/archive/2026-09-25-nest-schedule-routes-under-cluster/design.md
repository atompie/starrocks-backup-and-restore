## Context

`src/starrocks_br/api/routes/schedules.py` currently defines two routers: `schedule_router` (prefix `/schedule`, singular-id routes) and `schedules_router` (prefix `/schedules`, list + run-due). `cluster_id` is a body field on `ScheduleCreate` and a plain FK column read off the `Schedule` row for `Read`/`Update`. `jobs.py` and `inventory_groups.py` instead define routes with explicit `/cluster/{cluster_id}/...` path strings and call the shared `get_cluster_or_404(db, cluster_id)` helper from `_cluster_connect.py` before doing anything else. See proposal.md for the motivation.

## Goals / Non-Goals

**Goals:**
- Route paths for schedule CRUD match the `/cluster/{cluster_id}/...` shape used by `jobs.py` and `inventory_groups.py`, including reusing `get_cluster_or_404`.
- A schedule looked up, updated, or deleted through a `cluster_id` it doesn't belong to returns 404 (not a cross-cluster leak, not a 200 for someone else's schedule).
- `run-due` stays a single global endpoint - it's a batch operation over all clusters, not a per-cluster lookup, so it doesn't fit the `/cluster/{cluster_id}/...` shape and shouldn't be forced into it.

**Non-Goals:**
- No change to how `group_name` is stored or referenced (see proposal.md - no `Group` entity exists to add an id to).
- No API versioning or dual-route deprecation period - old paths are removed outright, not kept alongside new ones.
- No change to `Schedule`'s DB schema - `cluster_id` is already an FK column; this only changes how it arrives (path vs. body) and is validated per-request.

## Decisions

**Router shape**: Replace the `schedule_router`/`schedules_router` prefix-based split with one `APIRouter` (no fixed prefix) declaring full paths per route, the same style `inventory_groups.py` uses:
- `POST /cluster/{cluster_id}/schedules`
- `GET /cluster/{cluster_id}/schedules`
- `GET /cluster/{cluster_id}/schedule/{schedule_id}`
- `PATCH /cluster/{cluster_id}/schedule/{schedule_id}`
- `DELETE /cluster/{cluster_id}/schedule/{schedule_id}`
- `POST /schedules/run-due` (unchanged path, same router)

Alternative considered: keep two routers, add a `/cluster/{cluster_id}` prefix to one and leave `/schedules/run-due` on the other. Rejected only because it's marginally more code for no benefit over the single-router style `inventory_groups.py` already establishes - not a functional difference.

**Cluster/schedule mismatch handling**: `_get_schedule_or_404(db, cluster_id, schedule_id)` changes to also filter on `cluster_id` (`WHERE id = :schedule_id AND cluster_id = :cluster_id`), so a schedule fetched via the wrong cluster's path 404s exactly like a nonexistent schedule, rather than requiring a separate ownership check with a different status code. This mirrors how `inventory_groups.py` scopes every lookup by `cluster_id` in the query itself.

**Request body**: `ScheduleCreate.cluster_id` is removed; the route handler builds `Schedule(cluster_id=cluster_id, ...)` from the path parameter. `ScheduleUpdate` already omits `cluster_id` (schedules can't be moved between clusters today), so it needs no change.

**CLI**: `cli_api/schedule.py`'s `schedule_add`, `schedule_list`, and `schedule_remove` gain/reuse a `--cluster <cluster_id>` option and build the URL as `/cluster/{cluster_id}/schedules[/…]` instead of putting `cluster_id` in the JSON body or omitting cluster scoping on `list`.

## Risks / Trade-offs

- **[Risk]** Removing the cross-cluster `GET /schedules` is a breaking change for any caller currently relying on "list every schedule regardless of cluster" (e.g. a dashboard aggregating across clusters). -> **Mitigation**: proposal.md calls this out explicitly as **BREAKING** with no deprecation period; a caller needing a cross-cluster view can still call `GET /clusters` then `GET /cluster/{id}/schedules` per cluster, or list clusters first and fan out - consistent with how the same caller already has to fan out for jobs and inventory-groups today.
- **[Risk]** Existing tests/fixtures hardcode the old flat paths (`/schedule`, `/schedules`). -> **Mitigation**: tasks.md enumerates updating those call sites; this is mechanical (change the URL string and add the cluster fixture already used by job/inventory-group tests), not new test design.
