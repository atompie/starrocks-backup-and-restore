## Why

All four job-submission endpoints (`/backups/full`, `/backups/incremental`, `/restores`,
`/prunes`) share one `JobSubmitRequest` Pydantic model carrying every field any endpoint might
use. Each endpoint's OpenAPI schema therefore advertises fields it silently ignores (e.g.
`/backups/full` accepts `keep_last`/`snapshot`/`target_label` and does nothing with them), and
group/table/strategy validation that belongs at the request boundary currently only happens deep
inside the async job handler, surfacing as a job FAILED status instead of a request-time error.

## What Changes

- Replace `JobSubmitRequest` with four dedicated request models — `BackupFullRequest`,
  `BackupIncrementalRequest`, `RestoreRequest`, `PruneRequest` — each declaring only the fields
  its endpoint consumes, with `extra="forbid"` so posting a foreign field is a 422 instead of a
  silent no-op. **BREAKING**: clients posting fields another endpoint used to silently ignore
  (e.g. `keep_last` to `/backups/full`) now get a 422.
- `group` becomes a required, non-empty string on `BackupFullRequest` and
  `BackupIncrementalRequest` (was `str | None = None`). A missing/empty group is now rejected at
  request validation (422) rather than the existing cluster-existence check's 404. **BREAKING**:
  the missing-group case changes from 404 to 422 (an unknown-but-present group is still 404).
- Move the restore "at most one of group/table" check and the prune "exactly one strategy" check
  into Pydantic model validators, so they run synchronously at the HTTP boundary (422) instead of
  only inside the async job handler (job FAILED). The equivalent `handlers.py` runtime checks are
  kept as defense-in-depth for any non-HTTP caller of those handler functions.
- Remove `JobSubmitRequest` entirely; no backwards-compatible alias.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `api-job-execution`: request validation for job submission now happens synchronously at the
  HTTP boundary per endpoint (missing group, foreign fields, restore group/table exclusivity,
  prune strategy exclusivity all become 422 responses instead of either a later job failure or,
  for missing group, a 404).

## Impact

- `src/starrocks_br/api/schemas.py`: remove `JobSubmitRequest`, add the four new request models.
- `src/starrocks_br/api/routes/jobs.py`: update route signatures to use the new models; drop the
  now-unreachable `if not payload.group` 404 branch in `_submit_backup_job` (Pydantic already
  rejects a missing/empty group before the handler runs), keeping only the cluster
  group-existence 404 check.
- `src/starrocks_br/jobs/handlers.py`: read-only reference; its runtime `ValueError` checks are
  unchanged.
- `tests/test_api_jobs.py`: update existing payload tests and add coverage for the new 422 cases.
- Any docs/README referencing `JobSubmitRequest` or its combined field list.
