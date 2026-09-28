# Proposal

## Why

`src/starrocks_br/api/routes/clusters.py` and `src/starrocks_br/commands/clusters.py`
run raw SQLAlchemy queries (`db.query(...)`, `db.add(...)`, `db.flush()`,
`session.query(...)`) directly against the `Cluster`, `Job`, and `Schedule`
models instead of going through a data-access-layer module, violating the
architectural boundary in `AGENTS.md` ("Persistent metadata has a separate
data access layer in `src/starrocks_br/store/`" / commands call core
operations, not raw SQL). Every other local-metadata capability
(`jobs`, `inventory-groups`) already has its SQL isolated in
`src/starrocks_br/dal/metadata/`, with `commands/` calling that DAL and
routes calling `commands/`. Clusters is the one remaining capability where
routes and commands both embed raw queries.

## What Changes

- Add `src/starrocks_br/dal/metadata/clusters.py` holding every direct
  SQLAlchemy access needed for cluster metadata: create, list, get,
  update, delete, and the two existence checks (`has_active_job`,
  `has_enabled_schedule`) currently inlined in `commands/clusters.py`.
- Update `src/starrocks_br/commands/clusters.py` to add `create_cluster`,
  `list_clusters`, `get_cluster`, and `update_cluster` command functions
  that call the new DAL module, and rewrite `delete_cluster` to use the
  new `has_active_job`/`has_enabled_schedule` DAL helpers instead of its
  own inline queries.
- Update `src/starrocks_br/api/routes/clusters.py` so every route
  (`create_cluster`, `list_clusters`, `get_cluster`, `update_cluster`,
  `delete_cluster`) calls the corresponding `commands/clusters.py`
  function instead of touching the `Session`/`Cluster` model directly;
  routes keep only HTTP-status translation (404/409) and payload
  shaping.
- Update `src/starrocks_br/api/routes/_cluster_connect.py`'s
  `get_cluster_or_404` to resolve the cluster through the new DAL's
  `get_cluster` instead of `db.get(Cluster, cluster_id)` directly, since
  it is the shared cluster-lookup used by every route module.
- No behavioral change: HTTP status codes, response bodies, and existing
  error handling (`ClusterHasActiveJobError`,
  `ClusterHasEnabledScheduleError`, the duplicate-name 409) are preserved
  exactly.

## Capabilities

### New Capabilities

(none - no new user-facing behavior)

### Modified Capabilities

(none - `openspec/specs/api-cluster-registry/spec.md`'s externally
observable behavior is unchanged; this is an internal layering refactor)

## Impact

- `src/starrocks_br/dal/metadata/clusters.py` (new file)
- `src/starrocks_br/commands/clusters.py`
- `src/starrocks_br/api/routes/clusters.py`
- `src/starrocks_br/api/routes/_cluster_connect.py`
- Existing tests in `tests/unit/service/test_commands_clusters.py`,
  `tests/unit/service/test_api_clusters.py`, and
  `tests/unit/service/test_cluster_connect.py` continue to exercise the
  same public functions/routes and should pass unmodified; new
  `tests/unit/crud/test_dal_clusters.py` covers the new DAL module
  directly.
