## 1. Schema and model

- [ ] 1.1 Add nullable `Job.target_cluster_id` (FK `clusters.id`) and an Alembic migration; verify the migration applies on SQLite and `pytest tests/unit` for the store passes
- [ ] 1.2 Expose `source_backup_job_id` and `target_cluster_id` in `JobRead`; verify with a schema unit test

## 2. Submission and validation

- [ ] 2.1 Replace `RestoreRequest.target_label` with `source_job_id` plus optional `target_cluster_id`; keep group/table/database/rename_suffix rules; verify 422 unit tests
- [ ] 2.2 Validate synchronously in `commands/restore.py`: 404 (cluster/job/target, job not on path cluster), 409 (not a backup, not SUCCESS, data deleted, schedule pending deletion), 422 (incremental cross-cluster, group not on source cluster); verify one service test per rule
- [ ] 2.3 Live repository check at submit (source location via source cluster, match on target by location, 409 with name+location, 502/503 when unreachable); verify with mocked-cluster service tests including the different-name case
- [ ] 2.4 New routes `POST /restore/manual/cluster/{cluster_id}`, `GET /restore/history/cluster/{cluster_id}`, `GET /backup/job/{job_id}/restores`; retire `/backup/manual/restore/...`; verify API tests including the 404 on the old route

## 3. Execution

- [ ] 3.1 Resolve the chain from the source Job (full -> [source]; incremental -> [baseline_job, source]) and select tables from references on the source cluster (group `*` handling); verify service tests, including a non-latest job
- [ ] 3.2 Run the restore against the target cluster: connect to target, re-resolve the target repository name, `CREATE DATABASE IF NOT EXISTS`, then the existing rename-and-swap flow; verify with a mocked-StarRocks service test
- [ ] 3.3 Reserve and release the concurrency slot on the target cluster (release in `finally`); verify slot release on failure

## 4. Dispatch and reconcile on the target

- [ ] 4.1 Make admission, the RUNNING-per-cluster check and the handler cluster use `COALESCE(target_cluster_id, cluster_id)`; verify the "cross-cluster restore occupies the target" scenario in a test
- [ ] 4.2 Update stale-job reconcile (`_operation_targets`, `_fail_job`) to use the source job and target cluster instead of `find_restore_pair(label)`; verify with a reconcile test
- [ ] 4.3 Remove `find_restore_pair` / label-based lookups no longer used; verify `pytest tests/unit` passes

## 5. Tests and docs

- [ ] 5.1 Tests: failed or deleted source rejected; restore leaves the source job, events and references unchanged (SPEC §29); restore of a non-latest job; incremental chain; cross-cluster repository 409; incremental cross-cluster 422
- [ ] 5.2 Integration test restoring into a second cluster (skips cleanly when no second cluster is reachable); verify it runs or skips per `tests/integration/conftest.py`
- [ ] 5.3 Update `docs/` restore API description and note the breaking change; align `PLAN.md` §11 wording (`source_backup_job_id`, no `job_events`) in the same commit
