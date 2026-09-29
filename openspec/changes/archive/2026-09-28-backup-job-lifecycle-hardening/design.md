## Context

`commands/backup.py::run_backup_full` and `run_backup_incremental` share one shape: reserve the
`backup` concurrency slot once, then loop over the group's databases, and for each one build a
backup command, call `executor.execute_backup` (which submits to StarRocks and polls
`SHOW BACKUP` to completion), and record backup references. Today:

- The `execute_backup` call sits inside its own `with session_scope() as session:` block, so a
  metadata-store transaction is open for the full poll duration (`executor.poll_backup_status`
  can run for up to `MAX_POLLS * max_poll_interval` ≈ a day). `execute_backup` only touches that
  `session` argument in its `release_slot=True` branch, which no production caller exercises —
  both `run_backup_full` and `run_backup_incremental` always pass `release_slot=False`; only
  `tests/unit/service/test_executor.py` exercises `release_slot=True`.
- The slot reserved by `concurrency.reserve_job_slot(..., scope="backup", ...)` is only released
  (via `concurrency.complete_job_slot`) in two places: the loop's own success path (after all
  databases finish), and the `except _BackupLoopFailure` branch, which only catches failures
  reported through `execute_backup`'s own `{"success": False, ...}` result. Any other exception —
  `RuntimeError` when `planner.build_full_backup_command` returns nothing, a partition-resolution
  failure, or an error inside `planner.record_backup_references` / `_set_job_baseline` — escapes
  both branches uncaught. `jobs/thread_backend.py::_run_job` still catches it at the top level and
  marks the `Job` row `FAILED`, but the `run_status` row for the `backup` scope is left `ACTIVE`
  until the next `reserve_job_slot` call on that cluster happens to detect the label as stale via
  `dal.db.concurrency.is_backup_job_stale` (a live `SHOW BACKUP`/`SHOW DATABASES` round trip).
- `executor.execute_backup` returns immediately on a submit failure (`submit_backup_command`
  returning `False`) without calling `history.append_backup_event` at all, so that failure path
  produces a `FAILED` `Job` with zero `backup_history` rows.
- `dal.metadata.concurrency.complete_job` (called via `concurrency.complete_job_slot`) is already
  naturally idempotent: it looks up the `RunStatus` row by `(cluster_id, scope, label)` with no
  state guard and overwrites `state`/`finished_at`, so calling it more than once for the same job
  is safe.

## Goals / Non-Goals

**Goals:**
- No metadata-store session stays open while `execute_backup` polls StarRocks.
- The `backup` concurrency slot reserved for a job is released exactly once, on every exit path
  from `run_backup_full`/`run_backup_incremental` (success, an `execute_backup` failure, or any
  other exception), without relying on the next attempt's staleness self-heal.
- Every backup job that ends `FAILED` has at least one `FAILED` `backup_history` row with the
  real error message.
- The two bare `except Exception: pass` blocks in `executor.py` log instead of silently
  swallowing.

**Non-Goals:**
- No change to the staleness self-heal (`is_backup_job_stale`) itself — it remains a safety net
  for cases outside this process's control (e.g. a killed process), not the primary release path.
- No change to `restore.py`'s equivalent polling path — out of scope for this change; PLAN.md
  tracks restore hardening separately.
- No change to `execute_backup`'s public result shape (`{"success", "final_status",
  "error_message", ...}`) or to `poll_backup_status`.

## Decisions

**Drop `execute_backup`'s `session` parameter entirely**, rather than keeping it and just
shortening its lifetime. Its only use (`release_slot=True`'s `concurrency.complete_job_slot`
call) is already dead in production, and `execute_backup` already carries its own
`session_factory` (via `store.session.get_session_factory()`) for `history.append_backup_event`,
which opens and closes a short-lived session per call. Keeping a parameter whose only real caller
never uses it invites the same bug to come back. Callers stop wrapping the `execute_backup` call
in `session_scope()`; the two `with session_scope() as session:` blocks that build the backup
command and record references stay as they are today (they are already short-lived, bracketing a
single DB read/write, not the poll).

**Release the slot with one `try/finally` around the whole per-database loop**, not per
iteration and not duplicated across every failure branch. The loop reserves one slot for
potentially several databases (one `label` — the first database's), so the `finally` must sit
outside the `for`, at the same level the slot was reserved. Concretely:

```text
reserve_job_slot(..., primary_label)
try:
    for group_database in group_databases:
        ... build command, execute_backup(no session), record_backup_references ...
    # (incremental only) _set_job_baseline(...) also moves inside this try
except _BackupLoopFailure as failure:
    <record failure for re-raise after the finally>
finally:
    complete_job_slot(..., final_state=<computed from whether a failure was captured>)
if <failure captured>:
    _raise_for_backup_failure(...)
return {...}
```

Because `complete_job_slot` is already idempotent, this does not need to track "was the slot
already released" — a single call site is simply correct for every path, including one that
previously would have leaked. This also fixes a pre-existing related bug: `run_backup_incremental`
currently calls `_set_job_baseline` *after* its try/except but *before* releasing the slot; an
exception there today would leak the slot exactly like the other cases. Moving it inside the
`try` closes that too.

**Compute the released `final_state` from what actually happened**, not from
`_BackupLoopFailure` alone: `"FINISHED"` on the success path, and on any failure — whether it's a
caught `_BackupLoopFailure` (today's `(failure.result.get("final_status") or {}).get("state") or
"FAILED"`) or an uncaught exception bubbling through the `finally` — `"FAILED"`. An uncaught
exception passing through a `finally` block still allows the `finally` body to run before the
exception continues propagating, so this needs no separate `except Exception` catch-all; the
`finally` runs on every path, and code after the `try/finally` only executes when no exception
propagated out.

**Guarantee a `FAILED` history row for every failure**, by writing it from two places rather than
threading a single call through every failure type:
1. `executor.execute_backup`'s submit-failure branch calls `history.append_backup_event(job_id,
   "FAILED", message=submit_error)` before returning, mirroring what the poll-failure path
   already does.
2. `run_backup_full`/`run_backup_incremental` wrap the per-database loop body (the part outside
   `execute_backup`'s own error handling — command building, partition resolution, reference
   recording) so that an exception there also appends a `FAILED` `backup_history` row with
   `str(exception)` before the exception continues propagating to `thread_backend`, which still
   marks the `Job` row `FAILED` as it does today. This is a normal `except Exception as e: ...;
   raise` around that segment, not a change to the `finally` used for slot release — the two
   concerns (releasing a lock, and logging a history row) don't need to share a code path just
   because they both react to failure.

**Alternative considered**: have `execute_backup` itself always write a history row for the
non-StarRocks failures too, by moving the try/except up a level so everything funnels through
one function. Rejected: `execute_backup` doesn't know about command-building or
reference-recording — pulling that logic into it would blur the "StarRocks execution" vs. "backup
orchestration" boundary the current split already draws, for no benefit over handling it locally
in `commands/backup.py`.

**Logged instead of swallowed**: the two `except Exception: pass` blocks in `executor.py` (the
terminal `history.append_backup_event` call and the `release_slot=True` branch's
`complete_job_slot` call) become `except Exception: logger.error(...)`, consistent with how
`_on_progress`'s own `history.append_backup_event` failure is already logged a few lines above.

## Risks / Trade-offs

- [A failure inside the new `except`-and-reraise block that logs a `FAILED` history row could
  itself raise (e.g. a DB outage) while the slot's `finally` is also about to run] → the slot
  `finally` is a separate, outer block so slot release still happens even if the history write
  fails; the history-write failure is itself wrapped so it can't mask the original error passed
  to `thread_backend`.
- [Moving `_set_job_baseline` inside the `try` changes exactly when it runs relative to the loop
  finishing, for the multi-database incremental case] → it already only ever used the first
  database's resolved baseline id and ran after the whole loop; moving it a few lines earlier
  inside the same function, before the (also unchanged) `finally`, does not change that.

## Migration Plan

Internal-only change to job execution plumbing; no schema, API contract addition beyond the
history-completeness guarantee already covered by the spec delta, or data migration. Existing
`backup_history` rows are untouched. No feature flag or rollback beyond a normal revert, since
behavior only changes on failure paths that previously produced incomplete state (a leaked slot,
or a `FAILED` job with no history).
