# Design

## Context

See `proposal.md` - Why. Current state this design builds on:

- `Schedule.cadence`/`next_run_at` are `NOT NULL` today; every schedule is implicitly "recurring."
  `Schedule` has no `retention`/`expire_after_days` columns.
- `Job` has `group_id`, `result_json`, `label`, `repository` columns already, but no `schedule_id`
  or `baseline_job_id`, and `group_id`/`result_json` aren't exposed on `JobRead`.
- `commands/schedules.py` and `api/routes/schedules.py` already follow the intended
  routes-call-commands-call-dal layering (from the just-merged `move-metadata-sql-into-dal`
  change) - this design extends that layering, it doesn't change it.
- `commands.jobs.submit_job` / `dal.metadata.jobs.create_job` build a `Job` row from a cluster,
  job type, and a `params` dict; `group_id`/`repository` are pulled out of `params` because they
  are real job parameters the handler needs. `schedule_id` is lineage metadata the handler never
  reads, so it does not belong in `params` - it becomes an explicit function argument instead.
- `Job.label` is unknown at submission time and is written post-hoc, inside the running handler,
  via a small helper (`commands/backup.py::_set_job_label`) that opens its own short-lived
  `session_scope()` and calls `dal.metadata.jobs.set_label`. `baseline_job_id` has the identical
  shape (unknown until the handler resolves it) and reuses this exact pattern.
- SQLite foreign-key enforcement is not yet turned on for this project (`PLAN.md` §12.2 is
  unchecked); a non-SQLite deployment (Postgres/MySQL via `STARROCKS_BR_DATABASE_URL`) enforces FKs
  by default. This makes the `ondelete=` behavior chosen for the two new `Job` FKs matter *now*,
  not just after §12.2 - see Decisions.
- `PLAN.md` §5.7 describes `Job.schedule_id` as "FK `ON DELETE CASCADE`." This design deviates from
  that literal wording - see Decisions, "Job.schedule_id's ondelete is SET NULL, not CASCADE."

## Goals / Non-Goals

**Goals:**
- Every rule in the two spec deltas (`api-scheduling`, `api-job-execution`) is fully implementable
  without touching code this change doesn't already list.
- One-shot and recurring schedules share the same create/update/delete/read code paths wherever
  their behavior doesn't actually differ.
- `baseline_job_id`/`schedule_id` plumbing follows the existing `label`-setting precedent exactly,
  so a reader who already understands that code understands this too.

**Non-Goals:**
- No automatic expiry sweep, no scheduler loop (`PLAN.md` §9) - `expire_after_days` is stored and
  returned, never acted on, by this change.
- No retention enforcement/pruning job (`PLAN.md` §10) - `retention` is stored, validated, and
  returned, never acted on.
- No schedule-delete cascade to jobs/history/S3 snapshots (`PLAN.md` §8.2-8.4) - delete stays
  today's plain row delete.
- No change to `/backup/manual/full`, `/backup/manual/incremental`, or `/backup/manual/prune`
  (`PLAN.md` §8.1).

## Decisions

**Schedule-shape validation lives in `commands/schedules.py`, not in Pydantic `model_validator`s.**
`ScheduleCreate` gets simple single-field constraints (`retention: int | None = Field(default=None,
ge=1)`, `expire_after_days: int | None = Field(default=None, ge=1)`, `cadence: str | None = None`).
The cross-field rules (Q3 rejection, retention required/forbidden, expire_after_days one-shot-only,
cadence-to-null-on-update rejection) live in one shared function,
`commands.schedules._validate_schedule_shape(job_type, cadence, retention, expire_after_days)`,
raising a new `exceptions.InvalidScheduleFieldsError` that the route maps to 422 (same pattern as
the existing `InvalidCadenceError` → 422 translation already in `api/routes/schedules.py`).
**Why not a Pydantic `model_validator`**, matching `PruneRequest._check_exactly_one_strategy`:
`PruneRequest` is a stateless one-shot request - everything it validates is in the request itself.
`ScheduleUpdate` is a PATCH (`exclude_unset=True`) - validating "does this update leave the
schedule's *resulting* state consistent" requires the *existing* row's values for fields the
request didn't touch (e.g. updating `job_type` to `backup_incremental` without touching
`retention`). Pydantic can't see the existing row, so this logic has to run after the update is
merged onto the loaded `Schedule`, in the command layer - putting create-time validation in
Pydantic and update-time validation in the command layer would mean maintaining the same four
rules twice, in two different mechanisms, and having them drift.

**One-shot immutability and the cadence-to-null rejection are separate checks from schedule-shape
validation**, both in `commands.schedules.update_schedule`, run in this order:
1. If the existing schedule's `cadence` is already null (it's a one-shot), raise
   `exceptions.ScheduleImmutableError` (409) regardless of what the update touches.
2. If the update would set `cadence` to null on a schedule whose existing `cadence` is not null,
   raise `InvalidScheduleFieldsError` (422) - converting a schedule's shot-type is not supported.
3. Merge the update onto the existing schedule's field values and run
   `_validate_schedule_shape` on the result.

**`Job.schedule_id` and `Job.baseline_job_id` are threaded very differently, matching when each is
known:**
```
schedule_id: known at submit time              baseline_job_id: known only mid-execution
--------------------------------------          -------------------------------------------
run_due_schedules                                run_backup_incremental (in the thread pool)
      |                                                  |
      v                                          planner.find_recent_partitions(...)
submit_job(..., schedule_id=schedule.id)                 |  resolves baseline Job, returns
      |                                                  |  (partitions, baseline_job_id)
      v                                                  v
create_job(..., schedule_id=schedule_id)         _set_job_baseline(job_id, baseline_job_id)
      |  Job row created with schedule_id set             (opens its own session_scope(),
      v                                                    mirrors _set_job_label exactly)
  Job.schedule_id = <value>                       Job.baseline_job_id = <value>, written after
                                                    the Job row already exists
```
`submit_job`/`create_job` both gain an explicit `schedule_id: int | None = None` parameter (not a
`params` key). One-shot creation (`commands.schedules.create_schedule`, when `cadence is None`)
calls `submit_job` the same way `run_due_schedules` does, passing `schedule_id=schedule.id`, and
records the returned job's id on `schedule.last_run_job_id` the same way `run_due_schedules` does -
one-shot creation is "run due right now for a schedule due exactly once," reusing that shape
rather than inventing a second one.

**`planner.find_recent_partitions`'s return shape changes from `list[dict]` to `tuple[list[dict],
int | None]`** (partitions, resolved baseline job id - `None` only if the codebase later adds a
baseline resolution path that doesn't correspond to a `Job` row, which doesn't exist today, but the
signature stays honest rather than asserting non-null). Both of its two early-`return []` points
(no group tables, no tables in the target database) become `return ([], baseline_job_id)`, since
the baseline is always resolved before those checks run. `planner.find_latest_full_backup`'s
returned dict gains a `"job_id"` key alongside `label`/`backup_type`/`finished_at`, since that's
where the "no explicit baseline given" path gets the id from. A plain tuple, not a small dataclass,
to match this codebase's existing style for multi-value returns (e.g.
`dal/metadata/concurrency.py`'s tuples, `run_due_schedules`'s `tuple[list[int], int]`).
`run_backup_incremental` (`commands/backup.py`) unpacks the tuple and calls a new
`_set_job_baseline(job_id, baseline_job_id)` helper, defined next to and shaped exactly like
`_set_job_label`.

**`Job.schedule_id`'s `ondelete` is `SET NULL`, not `CASCADE`** - this corrects `PLAN.md` §5.7's
literal wording. Reasoning:
- `SPEC.md` §13-14 makes a `Job` a permanent historical record regardless of what happens to the
  schedule that created it ("a failed Job is not deleted... remains in the history"). A DB-level
  cascade would silently delete job history the moment a schedule row is deleted - `SPEC.md` §24's
  actual cascade-delete design explicitly deletes jobs (and drops their S3 snapshots first) through
  a *tracked, asynchronous* `schedule_cleanup` job (`PLAN.md` §8.2), never through a bare DB
  cascade a StarRocks call can't participate in. Using DB `CASCADE` today would let jobs vanish
  without their S3 snapshots ever being dropped - an orphaned-data bug, not a shortcut.
  - `CASCADE` would also be actively destructive under this change specifically: a one-shot
    schedule always has exactly one job attached (submitted at creation), and this change requires
    `DELETE` on a one-shot schedule to succeed (spec: "Deleting a schedule ... removes it the same
    way deleting a recurring schedule does"). `RESTRICT` is ruled out for the same reason (it would
    make every one-shot delete fail). `SET NULL` is the only option under which today's delete
    keeps working unchanged while never silently destroying job history.
  - This isn't just an interim choice: even after `PLAN.md` §8.2's `schedule_cleanup` job exists,
    it will delete `Job` rows explicitly (after dropping their snapshots) rather than relying on
    this FK at all - so `SET NULL` remains correct after that section lands, not only until then.
- `Job.baseline_job_id`'s `ondelete` is also `SET NULL` for the same reason: deleting the baseline
  full job (however that eventually happens) must not delete the incremental job that depended on
  it; it should just lose the link, which is the same reasoning as the one-shot immutability
  exemption already carved into §21-22 for retention.

**`commands.schedules.create_schedule`'s signature changes from taking `cluster_id: int` to taking
the loaded `cluster: Cluster`** (still keyword-only for the rest). One-shot creation needs to call
`submit_job(db, cluster, ...)`, which needs the `Cluster` object (to resolve
`cluster.default_backend`), and the route already has it in hand from `get_cluster_or_404` before
calling the command - passing it through avoids a redundant re-fetch inside the command.

## Risks / Trade-offs

- [Existing tests that create a recurring full-backup schedule without `retention` now get 422]
  → Expected and listed as BREAKING in the proposal; `tests/unit/service/test_api_schedules.py`'s
    and `test_commands_schedules.py`'s existing payloads are updated as part of this change's
    tasks, not left to fail.
- [`find_recent_partitions`'s signature change ripples into every existing caller/test] →
  Contained to one function with one call site in production code
  (`commands/backup.py::run_backup_incremental`); `tests/unit/service/test_planner.py`'s
  `find_recent_partitions` assertions are updated in the same task that changes the function.
- [SQLite vs. a real FK-enforcing backend could behave differently for these two new FKs until
  `PLAN.md` §12.2 turns on `PRAGMA foreign_keys=ON`] → Mitigated by choosing `SET NULL` (never
  destructive either way) instead of `CASCADE` (destructive on an FK-enforcing backend, silently
  inert on today's SQLite) - see Decisions. Not fully eliminated: on SQLite today, an orphaned
  `schedule_id`/`baseline_job_id` value would just sit there rather than being nulled, since nothing
  enforces the FK yet. That's already true of every other FK in this schema before §12.2, so it's
  an accepted, pre-existing condition, not a new one.
- [`retention`/`expire_after_days` are fully inert after this change - a reviewer could read
  "add these fields" and expect them to *do* something] → `proposal.md`'s "What Changes" and this
  design's Non-Goals both say so explicitly; the two spec deltas only ever describe storing and
  validating them, never acting on them.

## Migration Plan

One Alembic migration on top of the current head (`346a8270cb5f`):
- `schedules.cadence` → nullable
- `schedules.next_run_at` → nullable
- `schedules.retention` → new, nullable `INTEGER`
- `schedules.expire_after_days` → new, nullable `INTEGER`
- `jobs.schedule_id` → new, nullable `INTEGER`, FK → `schedules.id` `ON DELETE SET NULL`, indexed
- `jobs.baseline_job_id` → new, nullable `INTEGER`, self-referential FK → `jobs.id`
  `ON DELETE SET NULL`, indexed

No backfill needed: every new/loosened column is nullable, and existing rows simply read as
"recurring, no retention recorded, no schedule/baseline linkage" - consistent with what's actually
true of them. No downgrade data loss beyond dropping the new columns/constraints (standard
Alembic `downgrade()`, symmetric with `346a8270cb5f`'s style).

Rollback: revert the migration (drops the new/loosened columns) and revert the application code in
the same deploy - there is no intermediate state where only one side is deployed, since this is a
single change.

## Open Questions

None - every ambiguity this design surfaced changed the approach and was resolved above rather than
deferred (this is the point of §0c's Q9-Q12 and the `Job.schedule_id` FK correction above).
