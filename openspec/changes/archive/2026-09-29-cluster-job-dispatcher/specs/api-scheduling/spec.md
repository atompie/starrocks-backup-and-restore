## MODIFIED Requirements

### Requirement: Running due schedules submits jobs through the standard job system
The system SHALL support triggering due schedules either through an HTTP endpoint (`POST /backup/schedules/run`) or through a process-local CLI tick command (`starrocks-br-scheduler tick`). When triggered through either mechanism, the system SHALL find all enabled recurring schedules whose next-run time has passed, submit a job for each through the standard job-submission path (including backend selection, the schedule's inventory group id, and the schedule's repository), record that schedule as the job's origin, and advance each triggered schedule's next-run time to the next future occurrence after the current time. A submitted job is queued as `PENDING` and starts only when a scheduler tick admits it. The system SHALL NOT submit a new job for a due schedule that already has a `PENDING` or `RUNNING` job; it SHALL still advance that schedule's next-run time. The system SHALL exclude one-shot schedules (`cadence` null) from recurring due-schedule evaluation entirely: a one-shot schedule's only backup job is submitted once, at creation, and it has no recurring next-run time to evaluate. This exclusion governs only recurring backup submission; it does not exempt a one-shot schedule from expiry cleanup, which is evaluated and submitted separately through `expire_due_schedules` (see `retire-manual-backup-routes-and-schedule-cleanup`).

#### Scenario: Due schedule is triggered
- **WHEN** the run-due endpoint or CLI tick command is invoked and a recurring schedule's next-run time has passed
- **THEN** the system submits a job for that schedule's cluster/job-type/inventory group id/repository, records that schedule as the job's origin, and updates the schedule's next-run time to the next occurrence after now

#### Scenario: Not-yet-due schedule is skipped
- **WHEN** the run-due endpoint or CLI tick command is invoked and a recurring schedule's next-run time has not yet passed
- **THEN** the system does not submit a job for that schedule and leaves its next-run time unchanged

#### Scenario: No schedules are due
- **WHEN** the run-due endpoint or CLI tick command is invoked and no enabled recurring schedule is due
- **THEN** the system completes successfully indicating zero jobs were triggered

#### Scenario: Run-due call is idempotent per due occurrence
- **WHEN** due schedule execution is triggered twice in quick succession before a triggered job's schedule advances its next-run time
- **THEN** the second invocation does not submit a duplicate job for a schedule already triggered for its current due occurrence

#### Scenario: One-shot schedule is never selected by run-due
- **WHEN** the run-due endpoint or CLI tick command is invoked and a one-shot schedule exists
  (regardless of when it was created)
- **THEN** the system does not submit a recurring backup job for it and does not advance a next-run
  time for it; this scenario governs only recurring backup submission and is independent of
  one-shot expiry cleanup, which evaluates and may act on the same schedule through its own
  `expire_due_schedules` path

#### Scenario: Overdue schedule does not stack jobs
- **WHEN** a recurring schedule becomes due while its previous job is still `PENDING` or `RUNNING`
- **THEN** the system submits no additional job for it and advances its next-run time to the next occurrence

#### Scenario: Run-due only queues jobs
- **WHEN** a client calls `POST /backup/schedules/run` and a schedule is due
- **THEN** the created job is `PENDING` and starts only when a scheduler tick admits it
