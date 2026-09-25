## Why

The API mixes singular and plural nouns inconsistently for single-resource paths (`/clusters/{id}`, `/schedules/{id}`, `/jobs/{id}`, `/clusters/{id}/repositories/{name}`, `/clusters/{id}/inventory-groups/{group_name}`), while collection endpoints are correctly plural. Aligning single-resource routes to singular nouns and reserving plural nouns for collections only makes the API's naming convention predictable and removes the awkwardness of reading `/clusters/5` as "one of the clusters" versus `/clusters` as "the clusters".

## What Changes

- **BREAKING**: All single-resource routes move from plural to singular path segments; collection routes (`GET /clusters`, `GET /schedules`, `GET /cluster/{id}/repositories`, `GET /cluster/{id}/inventory-groups`) and the `POST /schedules/run-due` special action keep their plural/existing form.
- `clusters.py`: split into two routers — a `/cluster` router (POST, GET/{id}, PATCH, DELETE) and a `/clusters` router (GET collection only).
- `schedules.py`: split into two routers — a `/schedule` router (POST, GET/{id}, PATCH, DELETE) and a `/schedules` router (GET collection, POST run-due).
- `jobs.py`: job-submission routes move from `/clusters/{cluster_id}/...` to `/cluster/{cluster_id}/...`; `GET /jobs/{job_id}` becomes `GET /job/{job_id}`.
- `repositories.py`: routes move from `/clusters/{cluster_id}/repositories...` to `/cluster/{cluster_id}/repositories...` (collection path segment `repositories` stays plural; only the `clusters` -> `cluster` prefix changes).
- `inventory_groups.py`: routes move from `/clusters/{cluster_id}/inventory-groups...` to `/cluster/{cluster_id}/inventory-groups...` (same prefix-only change; `inventory-groups` and `tables` segments stay plural as collections).
- `app.py`: registers the newly split routers.
- Internal CLI clients (`cli_api/cluster.py`, `cli_api/job.py`, `cli_api/repository.py`, `cli_api/schedule.py`) updated to call the new paths.
- No compatibility aliases for old paths — old routes stop resolving.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
(none — this changes route paths only, not the request/response behavior described in `api-cluster-registry`, `api-job-execution`, `api-repository-management`, `api-scheduling`, or `api-inventory-groups`)

## Impact

- Routes: `src/starrocks_br/api/routes/clusters.py`, `jobs.py`, `repositories.py`, `schedules.py`, `inventory_groups.py`
- App assembly: `src/starrocks_br/api/app.py`
- Internal CLI clients: `src/starrocks_br/cli_api/cluster.py`, `job.py`, `repository.py`, `schedule.py`
- Tests: `tests/test_api_clusters.py`, `test_api_jobs.py`, `test_api_repositories.py`, `test_api_schedules.py`, `test_api_inventory_groups.py`, `test_api_auth.py`, `test_repository_sql.py`
- Docs: `docs/commands.md`, `docs/core-concepts.md`, `docs/getting-started.md`, `docs/api.md`
- Any external client calling the old plural single-resource paths breaks (no aliases kept), per explicit instruction.
