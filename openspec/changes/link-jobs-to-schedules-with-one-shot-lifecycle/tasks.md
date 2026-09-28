# Tasks

## 1. Schema

- [x] 1.1 In `src/starrocks_br/store/models.py`: make `Schedule.cadence` and `Schedule.next_run_at`
      nullable; add `Schedule.retention: int | None` and `Schedule.expire_after_days: int | None`
      (both nullable `Integer`); add `Job.schedule_id: int | None` (FK `schedules.id`,
      `ondelete="SET NULL"`, indexed) and `Job.baseline_job_id: int | None` (self-referential FK
      `jobs.id`, `ondelete="SET NULL"`, indexed). Verify with
      `python -c "from starrocks_br.store import models"`.
- [x] 1.2 Generate an Alembic migration on top of the current head (`346a8270cb5f`) covering every
      change in 1.1, with a symmetric `downgrade()`. Verify with `alembic upgrade head` then
      `alembic downgrade -1` against a scratch SQLite file, both succeeding.

## 2. Schedule-shape validation

- [x] 2.1 Add `exceptions.ScheduleImmutableError` (schedule id, for the 409 case) and
      `exceptions.InvalidScheduleFieldsError` (message, for the 422 cases) to
      `src/starrocks_br/exceptions.py`.
- [x] 2.2 Add `commands.schedules._validate_schedule_shape(job_type, cadence, retention,
      expire_after_days)` implementing: a one-shot (`cadence is None`) `backup_incremental`
      schedule is rejected (Q3); a recurring `backup_full` schedule requires `retention`; a
      recurring `backup_incremental` schedule forbids `retention`; a one-shot schedule forbids
      `retention`; `expire_after_days` is only accepted when `cadence is None`. Raises
      `InvalidScheduleFieldsError` on violation. Verify with new unit tests in
      `tests/unit/service/test_commands_schedules.py` covering all four `(shot-type, job_type)`
      combinations from the design's validation table plus the Q3 case, run via
      `pytest tests/unit/service/test_commands_schedules.py -q`.

## 3. Schema fields + create/update/delete wiring

- [x] 3.1 In `src/starrocks_br/api/schemas.py`: change `ScheduleCreate.cadence` to `str | None =
      None`; add `retention: int | None = Field(default=None, ge=1)` and `expire_after_days: int |
      None = Field(default=None, ge=1)` to `ScheduleCreate`/`ScheduleUpdate`; add the same two
      fields to `ScheduleRead`; change `ScheduleRead.cadence`/`next_run_at` to `| None`.
- [x] 3.2 Update `dal/metadata/schedules.py::create` to accept and persist `retention`/
      `expire_after_days` (and `cadence`/`next_run_at` as optional). Update
      `commands.schedules.create_schedule`'s signature to take the loaded `cluster: Cluster`
      (not `cluster_id: int`) plus `retention`/`expire_after_days`; call
      `_validate_schedule_shape` before persisting. When `cadence is None`, after persisting the
      schedule, call `submit_job(db, cluster, job_type, {"group_id": inventory_group_id,
      "repository": repository}, backend, schedule_id=schedule.id)` (see task 4.1 for the new
      `schedule_id` parameter) and set `schedule.last_run_job_id = job.id`, mirroring
      `run_due_schedules`.
- [x] 3.3 Update `commands.schedules.update_schedule`: raise `ScheduleImmutableError` (before
      applying any field) when the existing schedule's `cadence` is already `None`; raise
      `InvalidScheduleFieldsError` when the update would set `cadence` to `None` on a schedule
      whose existing `cadence` is not `None`; otherwise merge the update onto the existing
      schedule's current `job_type`/`cadence`/`retention`/`expire_after_days` and run
      `_validate_schedule_shape` on the merged result before applying it.
- [x] 3.4 Update `api/routes/schedules.py`: pass the already-fetched `cluster` (not `cluster_id`)
      into `commands.schedules.create_schedule`; catch `InvalidScheduleFieldsError` → 422 in both
      the create and update routes; catch `ScheduleImmutableError` → 409 in the update route.
- [x] 3.5 Confirm `dal.metadata.schedules.due_schedules` never selects a one-shot schedule (its
      `next_run_at` is `None`, which cannot satisfy `next_run_at <= now` in SQL); if the ORM/DB
      combination in use treats `NULL <= now` ambiguously, add an explicit
      `Schedule.cadence.is_not(None)` filter to be safe. Verify with a unit test asserting a
      one-shot schedule is never returned by `due_schedules`/never triggered by `run_due_schedules`
      even when its (null) `next_run_at` would otherwise seem "overdue."
- [x] 3.6 Tests in `tests/unit/service/test_api_schedules.py`: one-shot creation submits exactly
      one job and sets `last_run_job_id`; one-shot `backup_incremental` creation → 422; one-shot
      creation with `retention` set → 422; recurring `backup_full` creation without `retention` →
      422 (update the existing creation-payload tests that omit `retention` to include it); PATCH
      on an existing one-shot schedule → 409 regardless of which field is targeted; PATCH setting a
      recurring schedule's `cadence` to `null` → 422; PATCH changing `job_type` to
      `backup_incremental` while `retention` remains set → 422; DELETE on a one-shot schedule
      succeeds the same way DELETE on a recurring schedule does; a one-shot schedule is never
      triggered by `POST /backup/schedules/run`.

## 4. Job.schedule_id

- [x] 4.1 Add `schedule_id: int | None = None` to `commands.jobs.submit_job` and
      `dal.metadata.jobs.create_job`, persisting it on the created `Job` row.
- [x] 4.2 Update `commands.schedules.run_due_schedules` to pass `schedule_id=schedule.id` to
      `submit_job`.
- [x] 4.3 Add an optional `schedule_id` filter parameter to `dal.metadata.jobs.list_jobs` and
      `commands.jobs.list_jobs`; add a `schedule_id` query parameter to
      `GET /backup/history/cluster/{cluster_id}` in `api/routes/jobs.py`.
- [x] 4.4 Tests: `run_due_schedules` sets the triggered job's `schedule_id`
      (`tests/unit/service/test_commands_schedules.py`, extending the existing due-schedule test);
      one-shot creation's submitted job carries `schedule_id`
      (`tests/unit/service/test_api_schedules.py`); `list_jobs`'s `schedule_id` filter, including
      excluding jobs with no recorded `schedule_id`
      (`tests/unit/service/test_commands_jobs.py`); the API history endpoint's `schedule_id` filter,
      including the "excludes jobs submitted before schedule linkage existed" case
      (`tests/unit/service/test_api_jobs.py`).

## 5. Job.baseline_job_id

- [ ] 5.1 Add a `"job_id": job.id` key to the dict `planner.find_latest_full_backup` returns.
- [ ] 5.2 Change `planner.find_recent_partitions`'s return type from `list[dict]` to
      `tuple[list[dict], int | None]` (partitions, resolved baseline job id), threading the
      baseline job's id through both the explicit-`baseline_backup_label` path (already has the
      `Job` object via `backup_catalog.find_successful_job_by_label`) and the resolved-latest path
      (via 5.1's new `"job_id"` key); update both of its early-`return []` points to
      `return ([], baseline_job_id)`.
- [x] 5.3 Add `dal.metadata.jobs.set_baseline_job_id(db, job_id, baseline_job_id)`, matching the
      shape of the existing `set_label`.
- [ ] 5.4 Add `commands.backup._set_job_baseline(job_id, baseline_job_id)`, matching
      `_set_job_label` exactly (its own `session_scope()`, calls `jobs_dal.set_baseline_job_id`).
      Update `run_backup_incremental` to unpack `partitions, baseline_job_id =
      planner.find_recent_partitions(...)` and call `_set_job_baseline(job_id, baseline_job_id)`
      once resolved.
- [ ] 5.5 Update `tests/unit/service/test_planner.py`'s `find_latest_full_backup`/
      `find_recent_partitions` assertions for the new return shapes. Add a test in
      `tests/unit/service/test_commands_backup.py` asserting that after `run_backup_incremental`
      completes, the resulting `Job.baseline_job_id` matches the full job it was based on. Verify
      with `pytest tests/unit/service/test_planner.py tests/unit/service/test_commands_backup.py -q`.

## 6. Expose fields on JobRead

- [x] 6.1 Add `schedule_id`, `group_id`, `baseline_job_id`, and `result_json` fields to `JobRead`
      in `src/starrocks_br/api/schemas.py`.
- [x] 6.2 Tests in `tests/unit/service/test_api_jobs.py`: `GET /job/{id}` and
      `GET /backup/history/cluster/{id}` responses include all four new fields; a directly
      submitted job (no schedule) reports `schedule_id: null`; a full-backup job reports
      `baseline_job_id: null`; an incremental job reports the correct `baseline_job_id` once its
      handler completes.

## 7. Full-suite verification

- [ ] 7.1 Run the full unit suite (`pytest tests/unit -q`) and verify it passes, including every
      updated `ScheduleCreate` payload from task 3.6.
- [ ] 7.2 Re-run the migration round-trip from 1.2 (`alembic upgrade head` /
      `alembic downgrade -1`) against a fresh scratch database to confirm it still applies cleanly
      on top of the finished code.
