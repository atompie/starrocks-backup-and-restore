## 1. Executor: no held session, honest submit-failure history, logged errors

- [x] 1.1 Drop the `session: Session` parameter from `executor.execute_backup` (and the
  now-unreachable-in-production `release_slot`/`complete_job_slot` branch's use of it); verify
  `execute_backup`'s signature and every call site (including
  `tests/unit/service/test_executor.py`) no longer pass a session, and the existing test suite for
  `execute_backup` still passes.
- [x] 1.2 On a submit failure in `execute_backup` (`submit_backup_command` returns `False`), call
  `history.append_backup_event(job_id, "FAILED", message=submit_error)` before returning the
  failure result; add a unit test asserting a submit failure produces exactly one `FAILED`
  history row with the submit error message.
- [x] 1.3 Replace the two bare `except Exception: pass` blocks in `executor.py` (the terminal
  history write, and the `release_slot=True` branch's `complete_job_slot` call) with
  `except Exception: logger.error(...)`, matching the existing pattern used for `_on_progress`'s
  history write a few lines above; verify via a unit test that a forced failure in either call
  logs an error rather than raising or silently vanishing.

## 2. Commands: no held session, single slot-release path, failure history

- [x] 2.1 In `run_backup_full` and `run_backup_incremental`, stop wrapping the
  `executor.execute_backup` call in its own `with session_scope() as session:` block (now
  unnecessary since 1.1); verify no metadata-store session is open for the duration of a
  simulated long-running `execute_backup` call (e.g. a test that asserts no session is passed and
  none is held via a mocked `session_scope`/`get_session_factory`).
- [x] 2.2 Wrap the entire per-database loop in `run_backup_full` (from just after
  `reserve_job_slot` through the end of the loop) in a single `try/finally` that always calls
  `concurrency.complete_job_slot` exactly once, computing `final_state` as `"FINISHED"` on the
  success path and `"FAILED"` (or the failure's reported state, as today) on any exception,
  caught or not; remove the now-redundant slot-release call from the `except _BackupLoopFailure`
  branch since the `finally` covers it.
- [x] 2.3 Apply the same restructuring to `run_backup_incremental`, additionally moving
  `_set_job_baseline`'s call inside the `try` (before the `finally`) so a failure there is also
  covered by the single slot-release path.
- [x] 2.4 Add a unit test per function asserting the `backup` concurrency slot is released
  (`run_status` row no longer `ACTIVE`) when the failure is a `RuntimeError` raised before
  `execute_backup` is ever called (e.g. `build_full_backup_command`/`build_incremental_backup_command`
  returning nothing), not just when `execute_backup` itself reports failure.
- [x] 2.5 Add a unit test asserting the `backup` concurrency slot is released when
  `planner.record_backup_references` (or, for incremental, `_set_job_baseline`) raises after a
  successful `execute_backup` call.
- [x] 2.6 Wrap the loop body's non-`execute_backup` failure points (command building, partition
  resolution, reference recording, baseline setting) so that any exception there appends a
  `FAILED` `backup_history` row carrying `str(exception)` before re-raising (the `Job` row itself
  is still marked `FAILED` by `jobs/thread_backend.py`, unchanged); add a unit test asserting a
  job that fails this way has a non-empty history ending in a `FAILED` entry.

## 3. Spec and verification

- [x] 3.1 Run `python -m pytest tests/unit` and confirm all tests pass, including the new ones
  from sections 1 and 2.
- [x] 3.2 Manually trace both new spec scenarios in `specs/api-job-execution/spec.md` ("fails
  before submitting to StarRocks still records why", "fails to submit to StarRocks still records
  why") against the unit tests added in 1.2 and 2.6, confirming each scenario has a corresponding
  test.
