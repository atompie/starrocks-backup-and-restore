# Proposal

## Why

Today a `Schedule` has no count-based retention or one-shot lifecycle, and a `Job` has no link
back to the schedule that created it or to the full backup an incremental depends on. `SPEC.md`
§6-11 describes retention as a schedule property and a one-shot schedule (`cadence = null`) as a
first-class persisted entity with its own expiry; neither exists in the code yet. Building
`retention`/`expire_after_days` without also making one-shot schedules creatable would add fields
that cannot be exercised end-to-end (see `PLAN.md` §5's intro note and §0c, decisions Q9-Q12) -
this change does both together so nothing lands half-wired.

## What Changes

- Make `Schedule.cadence` and `Schedule.next_run_at` nullable. `cadence = null` marks a one-shot
  schedule (`SPEC.md` §7-8).
- Add `Schedule.retention` (int ≥ 1) and `Schedule.expire_after_days` (int ≥ 1, nullable = never
  expires) columns, both nullable at the DB level. Application-level validation in
  `ScheduleCreate`/`commands.schedules`: `retention` is required when `job_type=backup_full` and
  `cadence` is set (recurring full), forbidden otherwise (recurring incremental, and both one-shot
  cases); `expire_after_days` is only accepted when `cadence` is null (one-shot), forbidden for any
  recurring schedule. **BREAKING**: a recurring full-backup schedule creation request that omits
  `retention` is now rejected (422) where it previously succeeded.
- **BREAKING**: reject a one-shot (`cadence = null`) schedule whose `job_type` is
  `backup_incremental` with HTTP 422 (`SPEC.md` §8, decision Q3) - incrementals only come from
  recurring schedules.
- Creating a schedule with `cadence = null` persists it and immediately submits exactly one job
  through `commands.jobs.submit_job`, carrying that schedule's id.
- A one-shot schedule is immutable after creation: `PATCH` on it returns 409. `DELETE` is allowed,
  using the existing plain (non-cascading) row delete every schedule gets today - this change does
  not add snapshot/job cascade-on-delete (that depends on Backup References, not yet built; see
  `PLAN.md` §8).
- `run_due_schedules` skips any schedule where `cadence IS NULL` (one-shot schedules are never
  polled for their next occurrence - they run exactly once, at creation).
- Add `Job.schedule_id` (FK to `schedules.id`, `ON DELETE CASCADE`, nullable for every job type for
  now - the existing `/backup/manual/full`, `/backup/manual/incremental`, and
  `/backup/manual/prune` routes still submit schedule-less jobs). Set on every job `submit_job`
  creates on behalf of a schedule: both `run_due_schedules` and the new one-shot immediate-submit
  path.
- Add `Job.baseline_job_id` (FK to `jobs.id`, nullable) for incremental backup jobs, holding the id
  of the full job it was based on. Not known at submission time - resolved inside
  `run_backup_incremental` once the baseline is determined, and written post-hoc the same way
  `Job.label` already is (`dal.metadata.jobs.set_label`). Requires changing
  `planner.find_recent_partitions`'s return shape so the resolved baseline `Job`'s id is no longer
  discarded internally.
- Expose `schedule_id`, `group_id`, `baseline_job_id`, and `result_json` on `JobRead` (`group_id`
  and `result_json` are existing columns, not yet exposed). Add an optional `schedule_id` filter to
  `commands.jobs.list_jobs` / `dal.metadata.jobs.list_jobs` and to
  `GET /backup/history/cluster/{cluster_id}`.
- No change to schedule delete's HTTP contract (still `204`), to the manual backup routes, or to
  retention/expiry *enforcement* (no automatic expiry sweep, no retention pruning job) - those
  remain future work per `PLAN.md` §§8-10, which need the scheduler loop and Backup References
  this change does not add.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `api-scheduling`: schedule creation/update gains `retention`, `expire_after_days`, and nullable
  `cadence` (one-shot schedules), with the validation and immutability rules above. `ScheduleRead`
  gains the two new fields and `cadence`/`next_run_at` become nullable in the response shape.
- `api-job-execution`: `JobRead` gains `schedule_id`, `group_id`, `baseline_job_id`, `result_json`;
  the backup-history listing endpoint gains a `schedule_id` filter.

## Impact

- **Schema**: `Schedule.cadence`/`next_run_at` become nullable; new columns
  `Schedule.retention`, `Schedule.expire_after_days`, `Job.schedule_id`, `Job.baseline_job_id`.
  One new Alembic migration on top of the current head (`346a8270cb5f`).
- **Code**: `src/starrocks_br/store/models.py`, `src/starrocks_br/api/schemas.py`
  (`ScheduleCreate`/`Update`/`Read`, `JobRead`), `src/starrocks_br/commands/schedules.py` (creation
  validation, one-shot immediate submission, run-due skip), `src/starrocks_br/api/routes/schedules.py`
  (PATCH-immutability 409), `src/starrocks_br/commands/jobs.py` and
  `src/starrocks_br/dal/metadata/jobs.py` (`schedule_id` param on `submit_job`/`create_job`,
  `schedule_id` filter on `list_jobs`, new `set_baseline_job_id`), `src/starrocks_br/planner.py`
  (`find_recent_partitions`'s return shape), `src/starrocks_br/commands/backup.py`
  (`run_backup_incremental` calls `set_baseline_job_id`), `src/starrocks_br/api/routes/jobs.py`
  (`schedule_id` query param on the backup-history listing).
- **Not affected**: `/backup/manual/full`, `/backup/manual/incremental`, `/backup/manual/prune`
  (unchanged - still schedule-less submissions; retiring them is `PLAN.md` §8.1), schedule delete's
  cascade behavior (still the plain delete from today; the cleanup-job cascade is `PLAN.md` §8.2-8.4),
  the scheduler loop / automatic expiry sweep (`PLAN.md` §9, not built), retention enforcement /
  pruning (`PLAN.md` §10, not built).
- **Tests**: `tests/unit/service/test_api_schedules.py` and `tests/unit/service/test_commands_schedules.py`
  gain one-shot creation/immutability/run-due-skip cases and `retention`/`expire_after_days`
  validation-matrix cases; existing schedule-creation tests that omit `retention` need updating for
  the new required-field behavior. `tests/unit/service/test_api_jobs.py` gains `schedule_id` field/
  filter coverage. `tests/unit/service/test_commands_backup.py` and `tests/unit/service/test_planner.py`
  gain `baseline_job_id` coverage and adjust to `find_recent_partitions`'s new return shape.
