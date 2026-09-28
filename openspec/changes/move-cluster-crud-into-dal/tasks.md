# Tasks

## 1. DAL module

- [ ] 1.1 Create `src/starrocks_br/dal/metadata/clusters.py` with `create`,
      `list_all`, `get`, `update`, `has_active_job`,
      `has_enabled_schedule`, and `delete`, lifted from the current bodies
      of `commands/clusters.py::delete_cluster` and
      `api/routes/clusters.py`'s route handlers, and verify the module
      imports cleanly (`python -c "from starrocks_br.dal.metadata import clusters"`).
- [ ] 1.2 Add `tests/unit/crud/test_dal_clusters.py` covering `create`,
      `list_all`, `get` (found and not-found), `update`,
      `has_active_job` (true/false), `has_enabled_schedule` (true/false),
      and `delete`, using the same sqlite fixture pattern as
      `tests/unit/service/test_commands_clusters.py`; verify with
      `pytest tests/unit/crud/test_dal_clusters.py -q`.

## 2. Commands layer

- [ ] 2.1 Add `create_cluster`, `list_clusters`, `get_cluster`, and
      `update_cluster` to `src/starrocks_br/commands/clusters.py`, each
      calling the new DAL functions from task 1.1; `update_cluster` keeps
      the password-encryption decision (call `encrypt_password` only when
      a password was provided) before building the `updates` dict passed
      to `dal.clusters.update`.
- [ ] 2.2 Rewrite `delete_cluster` in `src/starrocks_br/commands/clusters.py`
      to call `dal.clusters.has_active_job` /
      `dal.clusters.has_enabled_schedule` / `dal.clusters.delete` instead
      of its inline `session.query(...)` calls, preserving the exact
      guard order (active job checked before enabled schedule) and raised
      exceptions (`ClusterHasActiveJobError`, `ClusterHasEnabledScheduleError`).
- [ ] 2.3 Run `pytest tests/unit/service/test_commands_clusters.py -q`
      unmodified and verify all three existing tests still pass.

## 3. Routes layer

- [ ] 3.1 Update `src/starrocks_br/api/routes/_cluster_connect.py`'s
      `get_cluster_or_404` to call
      `dal.metadata.clusters.get(db, cluster_id)` instead of
      `db.get(Cluster, cluster_id)`, keeping the same 404 behavior; verify
      with `pytest tests/unit/service/test_cluster_connect.py -q`.
- [ ] 3.2 Update `create_cluster`, `list_clusters`, and `update_cluster` in
      `src/starrocks_br/api/routes/clusters.py` to call the new
      `commands/clusters.py` functions instead of touching `db`/`Cluster`
      directly; keep the `IntegrityError` -> 409 translation in the route
      around the `commands.clusters.create_cluster` call.
- [ ] 3.3 Confirm `get_cluster`, `verify_cluster`, and `delete_cluster`
      routes already delegate fully to `commands`/`_cluster_connect`
      helpers (no direct `db.query`/`db.add` left in the file) and adjust
      if any raw access remains.

## 4. Verification

- [ ] 4.1 Run the full unit suite
      (`pytest tests/unit -q`) and verify it passes with no behavior
      changes, confirming `test_api_clusters.py`'s status-code and
      response-body assertions (201/200/204/404/409, password never
      echoed) are unaffected.
- [ ] 4.2 Grep for remaining direct metadata access outside the DAL for
      clusters (`grep -n "db.query\|session.query\|db.add\|db\.flush" src/starrocks_br/api/routes/clusters.py src/starrocks_br/api/routes/_cluster_connect.py src/starrocks_br/commands/clusters.py`)
      and verify it returns no matches referencing `Cluster`, `Job`, or
      `Schedule`.
