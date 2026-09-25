## 1. Cluster routes

- [x] 1.1 In `src/starrocks_br/api/routes/clusters.py`, split the single `APIRouter(prefix="/clusters")` into `cluster_router` (`prefix="/cluster"`, holding `POST ""`, `GET "/{cluster_id}"`, `PATCH "/{cluster_id}"`, `DELETE "/{cluster_id}"`) and `clusters_router` (`prefix="/clusters"`, holding `GET ""` only), per design.md Decisions; verify `python -c "import starrocks_br.api.routes.clusters"` succeeds
- [x] 1.2 In `src/starrocks_br/api/app.py`, replace `app.include_router(clusters.router)` with registration of both `clusters.cluster_router` and `clusters.clusters_router`; verify the app still builds via `python -c "from starrocks_br.api.app import create_app; create_app(check_env=False)"` (set required env vars first)

## 2. Job routes

- [x] 2.1 In `src/starrocks_br/api/routes/jobs.py`, change the four submission routes from `/clusters/{cluster_id}/...` to `/cluster/{cluster_id}/...`, and change `GET /jobs/{job_id}` to `GET /job/{job_id}`; verify with `grep -n "/clusters/\|/jobs/" src/starrocks_br/api/routes/jobs.py` returns nothing

## 3. Repository routes

- [x] 3.1 In `src/starrocks_br/api/routes/repositories.py`, change all three routes from `/clusters/{cluster_id}/repositories...` to `/cluster/{cluster_id}/repositories...`; verify with `grep -n "/clusters/" src/starrocks_br/api/routes/repositories.py` returns nothing

## 4. Schedule routes

- [x] 4.1 In `src/starrocks_br/api/routes/schedules.py`, split the single `APIRouter(prefix="/schedules")` into `schedule_router` (`prefix="/schedule"`, holding `POST ""`, `GET "/{schedule_id}"`, `PATCH "/{schedule_id}"`, `DELETE "/{schedule_id}"`) and `schedules_router` (`prefix="/schedules"`, holding `GET ""` and `POST "/run-due"` unchanged), per design.md Decisions; verify `python -c "import starrocks_br.api.routes.schedules"` succeeds
- [x] 4.2 In `src/starrocks_br/api/app.py`, replace `app.include_router(schedules.router)` with registration of both `schedules.schedule_router` and `schedules.schedules_router`; verify the app still builds via `python -c "from starrocks_br.api.app import create_app; create_app(check_env=False)"` (set required env vars first)

## 5. Inventory group routes

- [x] 5.1 In `src/starrocks_br/api/routes/inventory_groups.py`, change all six routes from `/clusters/{cluster_id}/inventory-groups...` to `/cluster/{cluster_id}/inventory-groups...`; verify with `grep -n "/clusters/" src/starrocks_br/api/routes/inventory_groups.py` returns nothing

## 6. Internal CLI clients

- [x] 6.1 In `src/starrocks_br/cli_api/cluster.py`, update the three request calls (`POST /clusters` -> `POST /cluster`, `GET /clusters` stays, `DELETE /clusters/{id}` -> `DELETE /cluster/{id}`); verify with a manual `starrocks-br api cluster add/list/remove` smoke test against a locally running server, or the corresponding CLI test if one exists
- [x] 6.2 In `src/starrocks_br/cli_api/job.py`, update the submit call's cluster-scoped path and both job-status `GET /jobs/{id}` calls to `GET /job/{id}`; verify via `grep -n "/clusters/\|/jobs/" src/starrocks_br/cli_api/job.py` returns nothing
- [x] 6.3 In `src/starrocks_br/cli_api/repository.py`, update all three request calls from `/clusters/{id}/repositories...` to `/cluster/{id}/repositories...`; verify with `grep -n "/clusters/" src/starrocks_br/cli_api/repository.py` returns nothing
- [x] 6.4 In `src/starrocks_br/cli_api/schedule.py`, update `POST /schedules` -> `POST /schedule`, `DELETE /schedules/{id}` -> `DELETE /schedule/{id}`; leave `GET /schedules` and `POST /schedules/run-due` unchanged; verify the diff matches this mapping

## 7. Tests

- [x] 7.1 Update `tests/test_api_clusters.py`, `tests/test_api_jobs.py`, `tests/test_api_repositories.py`, `tests/test_api_schedules.py`, `tests/test_api_inventory_groups.py`, `tests/test_api_auth.py`, and `tests/test_repository_sql.py` to call the new route paths; verify `pytest tests/ -k "cluster or job or repositor or schedule or inventory or auth"` passes
- [x] 7.2 Run the full test suite and verify it passes: `pytest`

## 8. Documentation and verification

- [x] 8.1 Update route references in `docs/commands.md`, `docs/core-concepts.md`, `docs/getting-started.md`, and `docs/api.md` to the new paths; verify with `grep -rn "/clusters/{cluster_id}\|/clusters/{id}\|/jobs/{job_id}\|/schedules/{schedule_id}" docs/` returns nothing (collection-only mentions of `/clusters`, `/schedules`, `/repositories`, `/inventory-groups` are expected to remain)
- [x] 8.2 Start the API server and fetch `/openapi.json` (or use FastAPI's interactive docs) to confirm every path listed matches the target route table in proposal.md, with no old plural single-resource paths and no duplicate/colliding operation IDs between the split routers; verify by diffing the path list against the proposal's target routes
