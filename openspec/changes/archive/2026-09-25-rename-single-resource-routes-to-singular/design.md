## Context

See proposal.md - Why. Two of the five route files (`clusters.py`, `schedules.py`) currently use a single `APIRouter(prefix=...)` covering every route in the file, including the collection `GET` that must stay plural while the rest of the routes become singular. The other three (`jobs.py`, `repositories.py`, `inventory_groups.py`) already declare full paths per route with no shared prefix, so they only need a string substitution (`/clusters/` -> `/cluster/`, `/jobs/` -> `/job/`).

## Goals / Non-Goals

**Goals:**
- Decide how `clusters.py` and `schedules.py` split their routers so a single prefix isn't forced across singular and plural paths.
- Keep the split consistent between the two files.

**Non-Goals:**
- No change to request/response schemas, status codes, or business logic in any route.
- No compatibility aliases for old paths (per proposal - explicit instruction).

## Decisions

**Two routers per file, one per prefix.**

`clusters.py`:
- `cluster_router = APIRouter(prefix="/cluster", tags=["clusters"], dependencies=[Depends(require_api_key)])` — `POST ""`, `GET "/{cluster_id}"`, `PATCH "/{cluster_id}"`, `DELETE "/{cluster_id}"`.
- `clusters_router = APIRouter(prefix="/clusters", tags=["clusters"], dependencies=[Depends(require_api_key)])` — `GET ""` (collection).

`schedules.py`:
- `schedule_router = APIRouter(prefix="/schedule", tags=["schedules"], dependencies=[Depends(require_api_key)])` — `POST ""`, `GET "/{schedule_id}"`, `PATCH "/{schedule_id}"`, `DELETE "/{schedule_id}"`.
- `schedules_router = APIRouter(prefix="/schedules", tags=["schedules"], dependencies=[Depends(require_api_key)])` — `GET ""` (collection), `POST "/run-due"`.

`app.py` registers both routers per file (`app.include_router(clusters.cluster_router)`, `app.include_router(clusters.clusters_router)`, and similarly for schedules) in place of the single current `include_router` call per file.

Alternative considered: drop the prefix entirely and write full paths per route (matching `jobs.py`'s existing style). Rejected because it makes each decorator longer and removes the only thing enforcing that every singular route in the file actually shares the same prefix — a typo in one route's literal path wouldn't be caught by anything. The two-router split keeps the prefix declared once per resource shape and lets each router's name signal which paths it owns.

**`jobs.py`, `repositories.py`, `inventory_groups.py`: literal path edits only**, no router restructuring — `/clusters/{cluster_id}/...` becomes `/cluster/{cluster_id}/...` in each `@router.<method>(...)` call, and `jobs.py`'s `GET /jobs/{job_id}` becomes `GET /job/{job_id}`. These files never shared a prefix across singular/plural routes, so there's nothing to split.

**Operation IDs**: no route currently sets an explicit `operation_id`; FastAPI derives it from the function name, which does not change. Operation IDs shift only because the route path segments they're partly derived from shift — no manual operation_id edits needed, but the generated OpenAPI doc should be diffed after the change to confirm no accidental collisions between the two routers per file (e.g. a route in both `cluster_router` and `clusters_router` sharing a function name).

## Risks / Trade-offs

[Old plural single-resource paths silently 404 instead of erroring loudly for any external caller not yet updated] -> Acceptable per explicit "no compatibility aliases" instruction in proposal.md; call out the breaking change in the PR description.

[Two routers per file is a new pattern not used elsewhere in the codebase (every other route file has exactly one router)] -> Contained to `clusters.py` and `schedules.py` only; the other three route files keep their existing single-router, explicit-path style unchanged.

## Migration Plan

No data migration. Deploy is a normal code release: update routes, internal CLI clients, tests, and docs together in one change; run the full test suite; regenerate/verify the OpenAPI doc; ship. No rollback complexity beyond reverting the commit if needed.
