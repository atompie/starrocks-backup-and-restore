## MODIFIED Requirements

### Requirement: Running due schedules submits jobs through the standard job system
The system SHALL support triggering due schedules either through an HTTP endpoint (`POST /backup/schedules/run`) or through a process-local CLI tick command (`starrocks-br-scheduler tick`). When triggered through either mechanism, the system SHALL find all enabled recurring schedules whose next-run time has passed, submit a job for each through the standard job-submission path (including backend selection, the schedule's inventory group id, and the schedule's repository), record that schedule as the job's origin, and advance each triggered schedule's next-run time to the next future occurrence after the current time. The system SHALL exclude one-shot schedules (`cadence` null) from due-schedule evaluation entirely: a one-shot schedule's only job is submitted once, at creation, and it has no recurring next-run time to evaluate.

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
- **WHEN** due schedule execution is triggered and a one-shot schedule exists (regardless of when it was created)
- **THEN** the system does not submit a job for it and does not modify it
