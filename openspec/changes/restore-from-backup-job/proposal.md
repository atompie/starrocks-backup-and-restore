## Why

Restore is still addressed by a backup *label* looked up on the restoring cluster, so it cannot express
"restore this Backup Job" (SPEC §25-27) and cannot restore into a different cluster (PLAN decision 0.7).
Operators need to pick any successful Backup Job and recreate its databases on another cluster.

## What Changes

- Restore requests identify the source by `source_job_id` (a successful Backup Job whose data is not
  deleted) instead of `target_label`, and may name an optional `target_cluster_id` (default: the source
  job's cluster).
- A Restore Job belongs to the source cluster (`Job.cluster_id`) and records where it restores to in a new
  nullable `Job.target_cluster_id`. Admission, concurrency and stale-job recovery act on the target cluster.
- Cross-cluster restore is supported for **full** backups only; an incremental source with a different
  target is rejected (422).
- The repository is never created by restore. At submit, the system checks live that the target cluster has
  a repository at the same location as the source's; otherwise it rejects with 409 naming the repository and
  its location, so the operator creates it through the existing repository endpoint.
- The restore creates the database on the target if it does not exist.
- `group_id` / `table` filters are resolved on the source cluster against the source job's references.
- The restore backup chain is resolved from the source Job (full -> itself; incremental -> its baseline and
  itself) instead of a label/time lookup.
- New routes `POST /restore/manual/cluster/{cluster_id}`, `GET /restore/history/cluster/{cluster_id}`,
  `GET /backup/job/{job_id}/restores`; **BREAKING**: `/backup/manual/restore/cluster/{cluster_id}` is
  retired and the request body changes from `target_label` to `source_job_id`.

## Capabilities

### New Capabilities

### Modified Capabilities
- `api-job-execution`: restore submission by source Backup Job and optional target cluster, cross-cluster
  rules, restore listing routes, admission on the target cluster, retirement of the manual restore route.

## Impact

- Code: `commands/restore.py`, `restore.py`, `dal/metadata/restore_catalog.py`, `commands/jobs.py`
  (dispatch, reconcile), `api/routes/jobs.py`, `api/schemas.py`, `store/models.py` + a new Alembic migration.
- API: breaking change to restore submission (route and body); new restore listing routes.
- Tests: unit tests for validation, chain resolution and cross-cluster repository checks; an integration test
  needing two StarRocks clusters sharing one S3 (see design.md).
- Docs: `PLAN.md` §11 wording (`job_events`, `source_job_id` name) should be aligned after this change.
