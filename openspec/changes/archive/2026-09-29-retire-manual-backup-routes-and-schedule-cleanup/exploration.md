  A deep exploration of the codebase, the planned change retire-manual-backup-routes-and-schedule-cleanup, and its downstream interaction with add-cli-scheduler-command surfaces several important gaps, edge
  cases, and test migration requirements.

  Executing retire-manual-backup-routes-and-schedule-cleanup first is the right sequence because it establishes:

  1. The one-shot schedule lifecycle as the exclusive full-backup entry point.
  2. The asynchronous schedule cleanup job (schedule_cleanup) and cascading snapshot/job removal.
  3. The one-shot schedule expiry logic evaluated during scheduler ticks.

  Below is the interaction topology and the specific gaps discovered across the codebase and specs.
  ──────
  ### Architecture & Interaction Flow

                          OPERATOR / API
                                |
               +----------------+----------------+
               | DELETE schedule                 | POST /backup/schedules/run
               v                                 v
       +--------------------+           +-------------------------+
       | Schedule Deletion  |           |     Scheduler Tick      |
       | Synchronous Guard  |           | (HTTP or CLI via lock)  |
       | - No active jobs   |           +------------+------------+
       | - No active restore|                        |
       | - No ext. baseline |           +------------+------------+
       +---------+----------+           |                         |
                 |                      v                         v
         (mark pending)        +-----------------+       +-----------------+
                 |             | Run Due Recur.  |       | Expire Due      |
                 v             | Schedules       |       | One-Shots       |
       +--------------------+  +--------+--------+       +--------+--------+
       | `schedule_cleanup` |           |                         |
       | Job Submitted      |           v (submits backup)        v (submits cleanup)
       +---------+----------+      +---------+               +---------+
                 |                 | Backup  |               | Schedule|
                 v                 |   Job   |               | Cleanup |
       +--------------------+      +---------+               +----+----+
       | Handler Execution  |                                     |
       | 1. Drop snapshots  | <-----------------------------------+
       | 2. Clear FKs &     |
       |    cascade jobs    |
       | 3. Delete Schedule |
       +--------------------+
  ──────
  ### 1. Missing Test Migration Tasks (Critical Test Suite Breakage)
  When task 4.1 removes /backup/manual/full and /backup/manual/incremental, at least 25 existing tests across 4 test files will immediately fail with 404 because they currently use the manual routes as their
  fixture setup mechanism:

  1. **test_api_jobs.py**:
      • Uses POST /backup/manual/full/cluster/{cluster_id} in over 15 tests to create sample jobs when verifying GET /job/{id}, GET /job/{id}/history, GET /job/{id}/references, status filtering, pagination, and
      invalid parameter validations.
      • These need to be migrated to use one-shot schedules (POST /backup/schedules/cluster/{cluster_id} with cadence=None) or direct submit_job.
  2. **test_api_clusters.py:114**:
      • Submits /backup/manual/full/cluster/{created['id']} to test cluster isolation.
  3. **test_backup_dispatch_parity.py**:
      • The entire test file was written specifically to assert dispatch parity between manual backup routes and scheduled runs.
      • With manual routes retired, this test file either needs to be updated to assert parity between one-shot schedule execution and recurring schedule execution, or retired.
  4. **test_full_backup_restore_cycle.py:129**:
      • The live integration test submits /backup/manual/full/cluster/{cluster_id} to take its baseline backup before restoring. It must be updated to create a one-shot schedule.


  │ Important
  │ tasks.md currently has no task for migrating existing test callers. It only mentions verifying retired routes return 404. Without updating existing tests, the test suite will fail.
  ──────
  ### 2. Contract Gap with add-cli-scheduler-command

  In add-cli-scheduler-command's tasks.md:11 (task 2.3) and design exploration, it explicitly expects:

    commands.schedules.expire_due_schedules(db, now)

  to exist in schedules.py.

  In retire-manual-backup-routes-and-schedule-cleanup's tasks.md:21 (task 4.3):

  │ "Process expired one-shot schedules from the scheduler tick through the shared validation and cleanup submission path..."

  What's missing to make this seamless:

  • Explicitly name and expose expire_due_schedules(session: Session, now: datetime.datetime) -> list[int] in commands/schedules.py.
  • Define what POST /backup/schedules/run returns: does RunDueResponse include schedule_cleanup job IDs in triggered_job_ids and increment triggered_count? (Returning all triggered job IDs, whether recurring
  backup or expiry cleanup, keeps the API contract clean and consistent).
  • Stale Scenario in add-cli-scheduler-command: Note that add-cli-scheduler-command/specs/api-scheduling/spec.md currently contains:
    #### Scenario: One-shot schedule is never selected by run-due
    - WHEN due schedule execution is triggered and a one-shot schedule exists (regardless of when it was created)
    - THEN the system does not submit a job for it and does not modify it
  This scenario was cloned before expiry was designed and directly contradicts one-shot expiry cleanup! Once retire-manual-backup-routes-and-schedule-cleanup merges its delta spec into main api-scheduling/spec.
  md, add-cli-scheduler-command's spec will need to be aligned.
  ──────
  ### 3. Database Foreign Key Constraint on Schedule.last_run_job_id

  In models.py:108:

    last_run_job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)

  Notice that last_run_job_id has no ondelete action (defaults to RESTRICT / NO ACTION).

  When schedule_cleanup deletes the schedule's backup jobs, if last_run_job_id points to the most recent backup job:

  • Any SQL DELETE FROM jobs WHERE id = ... will fail with a foreign key constraint violation.
  • design.md:32 mentions: "Clear Schedule.last_run_job_id before deleting schedule backup jobs because its current FK has no delete action."

  What should be tightened:

  1. In the Alembic migration, alter Schedule.last_run_job_id foreign key to ondelete="SET NULL".
  2. In the DAL deletion routine, explicitly set schedule.last_run_job_id = None; session.flush() before executing job deletion to ensure safety across SQLite and other SQL engines.
  ──────
  ### 4. Schemas Missing New Metadata Columns

  In retire-manual-backup-routes-and-schedule-cleanup:

  • Job.source_backup_job_id is added to track restore lineage.
  • Schedule.deletion_requested_at is added to mark schedules pending cleanup.

  However, neither schema in schemas.py currently mentions them:

  1. **JobRead** exposes baseline_job_id and schedule_id. It should also expose source_backup_job_id: int | None = None so consumers can inspect restore lineage.
  2. **ScheduleRead** should expose deletion_requested_at: datetime.datetime | None = None so clients querying GET /backup/schedules/... can observe pending deletion status.
  ──────
  ### 5. Mutation Guard on Pending-Deletion Schedules

  What happens if an operator calls PATCH /backup/schedules/cluster/{cluster_id}/schedule_id/{schedule_id} on a schedule that has deletion_requested_at IS NOT NULL?

  Currently:

  • update_schedule checks schedule.cadence is None to block updating one-shot schedules.
  • If a recurring schedule is pending deletion, allowing PATCH (e.g. changing enabled, cadence, etc.) would violate the lifecycle invariant.
  • Rule to add: update_schedule must reject requests with HTTP 409 if schedule.deletion_requested_at is not None.
  ──────
  ### 6. StarRocks Snapshot Deletion Edge Cases

  In spec.md:13:

  │ "A snapshot already absent from its repository SHALL count as dropped so a failed cleanup can be retried."

  In StarRocks, DROP SNAPSHOT ON <repo> WHERE SNAPSHOT = '<label>' throws a query execution error if the snapshot does not exist.
  The cleanup handler must either:

  • Run SHOW SNAPSHOT ON <repo> WHERE SNAPSHOT = '<label>' first and skip if absent, or
  • Catch StarRocks execution errors matching snapshot-not-found patterns and treat them as successful drops.
  • Furthermore, if a backup job failed before recording any BackupReference rows, the cleanup set of snapshots is empty; the handler must cleanly delete metadata without attempting StarRocks drops.
  ──────
  ### 7. Explicit DAL Queries for Deletion 409 Validation

  The synchronous pre-validation for schedule deletion needs three explicit DAL helper queries:

  1. has_active_backup_jobs(session, schedule_id) -> bool:
  Job.schedule_id == schedule_id and Job.status IN ('PENDING', 'RUNNING') (excluding schedule_cleanup jobs).
  2. has_active_dependent_restores(session, schedule_id) -> bool:
  Job.source_backup_job_id.in_(backup_job_ids) and Job.status IN ('PENDING', 'RUNNING').
  3. has_external_incremental_dependencies(session, schedule_id) -> bool:
  Job.job_type == 'backup_incremental', Job.status.in_(['PENDING', 'RUNNING', 'SUCCESS']), Job.baseline_job_id.in_(full_backup_job_ids), and (Job.schedule_id != schedule_id OR Job.schedule_id IS NULL).
  ──────
  ### Summary of What Should Be Refined in retire-manual-backup-routes-and-schedule-cleanup

   Area                     | Current Gap                                                                   | Recommended Refinement
  --------------------------|-------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------
   Test Migration           | Tasks don't mention migrating existing test callers (test_api_jobs.py,        | Add explicit sub-task under Section 4 / 5 in tasks.md to migrate tests to one-shot schedules.
                            | test_api_clusters.py, test_backup_dispatch_parity.py,                         |
                            | test_full_backup_restore_cycle.py)                                            |
   Scheduler Tick Contract  | Function signature and tick response for expiry not named                     | Name commands.schedules.expire_due_schedules(db, now) and include cleanup job IDs in RunDueResponse.
   Schedule.last_run_job_id | FK has no delete action; could cause constraint violation                     | Set ondelete="SET NULL" in migration and explicitly clear before job deletion in DAL.
   API Schemas              | source_backup_job_id and deletion_requested_at missing from read schemas      | Add source_backup_job_id to JobRead and deletion_requested_at to ScheduleRead.
   Schedule Mutation Guard  | PATCH on pending-deletion schedule not blocked                                | Add 409 check in update_schedule when deletion_requested_at is set.
   Downstream Delta Spec    | add-cli-scheduler-command has stale scenario stating one-shots are never      | Note for future update when retire-manual-backup-routes-and-schedule-cleanup is archived/synced.
                            | touched by run-due                                                            |
  ──────
  How would you like to proceed? We can update tasks.md and design.md in retire-manual-backup-routes-and-schedule-cleanup to capture these details before beginning implementation.

────────────────────────────────────────────────────────────
> See @[SPEC.md] and @PLAN.md form more details this task is to finish the Section 8.

● Read(~/PycharmProjects/dev/starrocks-backup-and-restore/PLAN.md) (ctrl+o to expand)

▸ Thought for 5s, 1.0k tokens
  Focusing on schedule retirement, manual backup endpoints will be replaced by one-shot schedules. A `schedule_cleanup` job will be introduced, dropping snapshots and deleting associated data, cascading deletes...

● Read(~/PycharmProjects/dev/starrocks-backup-and-restore/SPEC.md) (ctrl+o to expand)

▸ Thought for 8s
  The current codebase contains several unit tests that rely on the manual backup routes that are slated for retirement. These tests need to be updated to utilize one-shot schedules or job submission methods to...
  Comparing SPEC.md (specifically §8 and §24) and PLAN.md (Section 8) with the current codebase and OpenSpec change retire-manual-backup-routes-and-schedule-cleanup reveals exactly what is required to complete
  Section 8 before proceeding to Section 9 (add-cli-scheduler-command).
  ──────
  ### Alignment: PLAN.md Section 8 vs. OpenSpec Change

  PLAN.md defines Section 8 as:

    PLAN.md Section 8 Item           OpenSpec Tasks
    ----------------------           --------------
    8.1 Retire manual routes  <--->  Task 4.1 (API route removal)
    8.2 schedule_cleanup job  <--->  Tasks 1.2, 3.1, 3.2 (Handler, cascades, drop snapshots)
    8.3 Synchronous 409 guard <--->  Tasks 1.1, 2.1, 4.2 (Mark pending, 409 on active/baseline, 202)
    8.4 Expiry via tick       <--->  Task 4.3 (expire one-shots via scheduler tick)
    8.5 Unit/Service tests    <--->  Tasks 5.1, 5.2 (unit + integration coverage)
  ──────
  ### Findings: What is Missing or Needs Tightening for Section 8
  #### 1. Existing Test Migration (Critical for a Passing Test Suite)
  PLAN.md 8.1 states: "Retire /backup/manual/full and /backup/manual/incremental (no compatibility wrappers)".
  • In the current codebase, around 25 tests across test_api_jobs.py, test_api_clusters.py, and test_full_backup_restore_cycle.py call /backup/manual/full to set up sample jobs.
  • Furthermore, test_backup_dispatch_parity.py specifically tests parity between the retired manual routes and scheduled routes.
  • Missing in tasks.md: Task 4.1 only mentions asserting that retired routes return 404. It must explicitly include migrating existing test callers to one-shot schedules (POST
  /backup/schedules/cluster/{cluster_id} with cadence=None) and updating test_backup_dispatch_parity.py so the existing suite stays green.

  #### 2. Clean Interface for Section 9 (add-cli-scheduler-command)

  • PLAN.md 8.4 notes that expiry will be run by the scheduler tick in Section 9.
  • Section 9's tasks.md:11 (task 2.3) explicitly expects commands.schedules.expire_due_schedules(db, now) to exist in schedules.py.
  • Missing in Section 8 spec/design: Explicitly name and expose expire_due_schedules(session, now) in commands/schedules.py. In Section 8, the existing HTTP tick (POST /backup/schedules/run / run_due_schedules)
  should run both due recurring schedules and expire_due_schedules, including cleanup job IDs in RunDueResponse, so Section 9 can reuse expire_due_schedules directly.

  #### 3. Database Constraint on Schedule.last_run_job_id

  • In models.py:108, last_run_job_id has no ondelete action (RESTRICT/NO ACTION).
  • When deleting a schedule's backup jobs, any job referenced by last_run_job_id will fail with an FK constraint error if not handled.
  • Action: In the Alembic migration (task 1.1), update Schedule.last_run_job_id with ondelete="SET NULL", and in the DAL cleanup logic (task 1.2), explicitly set schedule.last_run_job_id = None; session.flush()
  before deleting backup jobs.

  #### 4. Expose New Attributes on Read Schemas
  • Job.source_backup_job_id (restore lineage) should be exposed as source_backup_job_id: int | None = None in JobRead, matching baseline_job_id.
  • Schedule.deletion_requested_at should be exposed in ScheduleRead so clients can observe pending deletion status before cleanup finishes.

  #### 5. Mutation Guard on Pending-Deletion Schedules
  • If a schedule has deletion_requested_at IS NOT NULL, PATCH /backup/schedules/cluster/{cluster_id}/schedule_id/{schedule_id} must be rejected with HTTP 409, preventing mutation of a schedule already marked
  for cleanup.

  #### 6. Resilient Snapshot Deletion in StarRocks

  • When dropping repository snapshots, StarRocks throws an error if a snapshot is not found.
  • The schedule_cleanup handler must check SHOW SNAPSHOT or catch absent-snapshot errors to treat already-dropped snapshots as successful, enabling safe retry.
  ──────
  ### Recommended Updates to Change Artifacts

  To capture these details into retire-manual-backup-routes-and-schedule-cleanup:

  1. design.md:
      • Add decision to expose commands.schedules.expire_due_schedules(db, now).
      • Document ScheduleRead and JobRead schema additions.
      • Document blocking PATCH on schedules with deletion_requested_at IS NOT NULL.
  2. tasks.md:
      • Add explicit sub-task under Section 4 to migrate existing unit/integration test callers from manual routes to one-shot schedules.
      • Add schema field updates to tasks 1.1 / 4.2.
      • Name expire_due_schedules explicitly in task 4.3.