## Why

`commands/backup.py`'s per-database backup loop holds a metadata-store session open for the
entire duration of `executor.execute_backup`'s StarRocks polling (up to a day), violating
AGENTS.md's rule that a database transaction must never stay open while StarRocks runs or a job
polls for completion. The same loop only releases its `backup` concurrency slot (`commands/
concurrency.reserve_job_slot`, scope `backup`) on the one failure path that flows through
`execute_backup`'s own result dict (`_BackupLoopFailure`); any other exception — a `RuntimeError`
from an empty backup command, or a failure in `planner.record_backup_references` — escapes
uncaught, leaks the slot, and blocks every later backup attempt on that cluster until the next
attempt's staleness check happens to heal it. Meanwhile a submit failure inside `execute_backup`
returns before writing any `backup_history` row at all, so a `Job` can end up `FAILED` with an
empty execution log and no recorded reason. Two `except Exception: pass` blocks in `executor.py`
silently swallow a failed history write and a failed slot release. This change closes all of
these gaps together since they sit in the same execution path and the fix for one (a single
`try/finally` wrapping the whole per-database loop) is also the fix for the others.

## What Changes

- Drop `executor.execute_backup`'s unused `session: Session` parameter (its `release_slot=True`
  path is the only thing that touches it, and that path is never used by production callers —
  only by tests); stop wrapping the `execute_backup` call in `commands/backup.py` inside a live
  `session_scope()`, so no metadata-store session stays open during polling.
- In `commands/backup.py::run_backup_full` and `run_backup_incremental`, wrap the entire
  per-database loop (not just `execute_backup`'s own failure branch) in a single `try/finally`
  that always releases the `backup` concurrency slot via `concurrency.complete_job_slot`
  (already idempotent at the DAL level — it upserts by `(cluster_id, scope, label)` with no state
  guard), regardless of which line raised.
- Guarantee that every backup job that ends `FAILED` has at least one `FAILED` `backup_history`
  row carrying the real error message: `executor.execute_backup` writes one on a submit failure
  (today it returns before writing anything), and `run_backup_full`/`run_backup_incremental`
  write one from their own exception handling for a failure that occurs outside
  `execute_backup` (building the backup command, resolving partitions, recording references,
  etc.).
- Replace the two bare `except Exception: pass` blocks in `executor.py` (the terminal history
  write, and the slot-release call in the now-dead `release_slot=True` branch) with logged
  errors.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `api-job-execution`: strengthens "A job's execution history can be retrieved" so that a job
  that ends `FAILED` always has at least one `FAILED` history entry with an error message, even
  when the failure occurred outside StarRocks polling (e.g. before a backup command could be
  submitted).

## Impact

- `src/starrocks_br/executor.py`: `execute_backup` signature (drop `session`), submit-failure
  history write, logged error handling.
- `src/starrocks_br/commands/backup.py`: `run_backup_full`, `run_backup_incremental` — session
  scoping around `execute_backup`, `try/finally` slot release, failure-path history write.
- `src/starrocks_br/concurrency.py` / `dal/metadata/concurrency.py`: no signature change;
  relying on `complete_job_slot`'s existing idempotency.
- Tests: `tests/unit/service/test_executor.py`, and backup-command-layer tests covering the
  concurrency slot and history behavior on each failure path.
