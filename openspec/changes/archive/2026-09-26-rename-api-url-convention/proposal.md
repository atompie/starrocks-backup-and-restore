## Why

The API's URL structure is inconsistent: most endpoints start with `/cluster/{cluster_id}/...` even when the actual domain being operated on is backups, schedules, repositories, or inventory groups. This makes the routing table hard to scan (everything looks like it's about clusters) and makes it awkward to add non-cluster-scoped operations later. We're establishing and enforcing a single convention, `/{domain}/{operation}/{context}/{identifiers}`, across the whole API before it grows further.

## What Changes

- **BREAKING**: Rename the "Jobs" section to "Manual Backups". Move `full`/`incremental`/`restore`/`prune` submission under `/backup/manual/{operation}/cluster/{cluster_id}`.
- **BREAKING**: Move schedule endpoints under `/backup/schedules/...`; `POST /schedules/run-due` becomes `POST /backup/schedules/run`.
- **BREAKING**: Move repository CRUD under `/repositories/cluster/{cluster_id}/...` (the standalone `/repositories/verify` endpoint is already domain-first and is unchanged).
- **BREAKING**: Split inventory-group endpoints by cardinality: collection endpoints move under `/inventories/cluster/{cluster_id}`, single-group CRUD (including table-membership sub-resources) moves under `/inventory/cluster/{cluster_id}/group_id/{group_id}`.
- Old `/cluster/{cluster_id}/...` paths for these four groups are removed outright; no compatibility aliases are kept (none exist for any other endpoint in this project today).
- `/cluster/{cluster_id}` (cluster CRUD itself), `/clusters`, `/clusters/verify`, `/health`, and `GET /job/{job_id}` are unchanged — their domain is already first, or (for the job-status poll) there is no natural context to nest it under since it serves every job type.
- Update the FastAPI route declarations, router tags, and OpenAPI-visible section names; update the `cli_api` HTTP client call sites; update `docs/api.md`, `docs/commands.md`, `docs/scheduling.md`; update the affected test suites.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. The existing specs (`api-job-execution`, `api-scheduling`, `api-repository-management`, `api-inventory-groups`, `cli-api-client`) describe endpoint behavior (what an operation accepts, validates, and returns) without naming literal URL paths as a requirement. This change only relocates paths; no requirement text changes. `skip_specs: true` is set in `.openspec.yaml` accordingly.

## Impact

- **Code**: `src/starrocks_br/api/routes/jobs.py`, `schedules.py`, `repositories.py`, `inventory_groups.py`; `src/starrocks_br/cli_api/client.py`, `job.py`, `repository.py`, `schedule.py`, `cluster.py` (path-building call sites only).
- **Docs**: `docs/api.md` (also gains the previously-missing "Inventory Groups" section), `docs/commands.md`, `docs/scheduling.md`.
- **Tests**: `tests/test_api_jobs.py`, `tests/test_api_schedules.py`, `tests/test_api_repositories.py`, `tests/test_api_inventory_groups.py`, `tests/test_cli_api_client.py`, `tests/integration/test_full_backup_restore_cycle.py`.
- **External clients**: any existing caller of the old paths breaks; this is intentional and there is no deprecation window (no compatibility-alias mechanism exists in this project to preserve one).
