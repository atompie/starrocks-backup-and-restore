## Why

`table_inventory` ("inventory groups") controls which tables a backup/restore/prune job operates
on, but today it has no REST API or CLI CRUD — the only way to populate it is a raw f-string SQL
bootstrap insert (SQL-injection risk) or manual SQL against the cluster. Worse, `POST
.../backups/full` and `.../backups/incremental` currently accept requests with no `group` at all,
return 202, and only fail later, asynchronously, with a cryptic `KeyError` once the job handler
reads `params["group"]`. This change adds a proper CRUD API for inventory groups and makes the
missing/unknown-group case fail fast and synchronously instead of as a deferred job failure.

## What Changes

- Add a new domain module (`inventory_groups.py`) and REST API for creating, listing, reading,
  and deleting inventory groups and their table memberships, scoped under
  `/clusters/{cluster_id}/inventory-groups`, mirroring the existing `repositories.py` pattern.
- Add a synchronous existence check for `group` before a `backup_full`/`backup_incremental` job is
  enqueued: an unknown or missing group now returns `404` immediately instead of `202` followed by
  an async job failure. **BREAKING**: callers that today submit `backup_full`/`backup_incremental`
  without pre-provisioning the group (relying on the async failure as their error signal) will now
  get a synchronous `404`. No backup with a missing/unknown group has ever completed successfully,
  so no working caller's outcome changes — only when and how the error is reported changes.
- Fix `schema.bootstrap_table_inventory`'s raw f-string SQL interpolation (SQL-injection risk) by
  routing inserts through the new domain module's parameterized `add_membership`.
- Harden `jobs/handlers.py`'s `run_backup_full`/`run_backup_incremental` to raise a clear
  `ValueError` (instead of an unlabeled `KeyError`) if `group` is ever absent from `params`, as
  defense-in-depth for non-HTTP callers.
- Deduplicate the `_connect`/`_connect_or_503`/`_get_cluster_or_404` helper trio (currently copied
  across `repositories.py`, `jobs.py`, `clusters.py`) into a shared module before adding a third
  copy of it for the new router.

## Capabilities

### New Capabilities
- `api-inventory-groups`: create, list, read, and delete inventory groups and their table
  memberships on a registered cluster via REST API, mirroring the `api-repository-management`
  CRUD pattern.

### Modified Capabilities
- `api-job-execution`: the "accept a job submission and return 202" requirement changes to require
  a synchronous existence check of the `group` parameter for `backup_full`/`backup_incremental`
  submissions, returning `404` before job creation when the group does not exist, instead of
  accepting any value and letting the job fail asynchronously.

## Impact

- New: `src/starrocks_br/inventory_groups.py`, `src/starrocks_br/api/routes/inventory_groups.py`,
  `src/starrocks_br/api/routes/_cluster_connect.py`.
- Modified: `src/starrocks_br/api/schemas.py`, `src/starrocks_br/api/routes/jobs.py`,
  `src/starrocks_br/api/routes/repositories.py`, `src/starrocks_br/api/routes/clusters.py`,
  `src/starrocks_br/api/app.py`, `src/starrocks_br/schema.py`, `src/starrocks_br/jobs/handlers.py`.
- No DDL/schema change to `table_inventory` itself.
- Out of scope: the per-endpoint request-schema split (`JobSubmitRequest` decomposition) and CLI
  commands for inventory groups are tracked separately; this change's `_submit_backup_job` helper
  is designed to be reused by that follow-on work.
