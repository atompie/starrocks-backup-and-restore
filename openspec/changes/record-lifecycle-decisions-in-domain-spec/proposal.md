## Why

`SPEC.md`'s domain model predates several lifecycle decisions made while planning the path to a
spec-complete system (see `PLAN.md`, sections 0/0b): retention now excludes incremental backups
and any full backup they depend on, retention itself runs as its own tracked operation, one-shot
schedules can expire automatically, and deleting a schedule or a cluster cascades to the backup
data those schedules produced. `AGENTS.md`'s concurrency notes are similarly out of date: they
describe backup serialization as a current *gap* to fix, when it is in fact the confirmed target
behavior, and they say nothing about how retention or a cascading delete fit into that
concurrency model. Recording these decisions in the domain spec and the architecture notes now,
before any schema or code changes, keeps every later change (job event log, schedule/cluster
delete cascades, schedule-scoped retention, restore-by-job) traceable to an agreed domain rule
instead of an implementation ad hoc choice.

## What Changes

- `SPEC.md`:
  - Add `Retention Job` to the domain model (§2) as a peer of Backup Job and Restore Job: it has
    its own id, status, and an append-only History, and it references the Schedule and the
    Backup Jobs whose data it drops.
  - Amend the Manual Schedule section (§8) to state that a one-shot schedule may define an
    automatic expiry (`never`, or after a configured number of days), after which it is deleted
    the same way an operator-deleted schedule is.
  - Amend Retention (§10-11) and Failed Jobs and Retention (§22) to state that retention
    considers only successfully completed **full** backups, that a full backup which is the
    baseline for any still-existing incremental backup is never dropped by retention regardless
    of the retention count, and that incremental backups themselves are never subject to
    retention.
  - Add a new short section describing schedule deletion (operator-initiated or automatic
    expiry) and cluster deletion as asynchronous operations: they are accepted synchronously
    (rejected up front if a job is still running, or if deleting would remove a full backup
    another schedule's incremental depends on) and then cascade in the background, deleting the
    schedule's/cluster's Backup Jobs, their History and References, and the corresponding
    snapshots in the Repository. A cluster can only be deleted once it has no Schedules at all
    (enabled or disabled).
  - Note, near Restore Job (§25), that a Restore Job is deleted along with the Backup Job it
    restored from, since it has no independent value once its source is gone.

- `AGENTS.md`:
  - Rewrite the "Parallel execution and metadata" paragraph: backup serialization per cluster
    (`concurrency.reserve_job_slot` scope `backup`) is the confirmed policy, not a gap to
    reconsider; a Retention Job shares that same `backup` scope so it can never run concurrently
    with a backup on the same cluster.
  - Add a note that schedule deletion and cluster deletion follow the same pattern as backup
    submission: an API call returns immediately after synchronous validation, and the actual
    StarRocks/S3 cleanup runs as a job — consistent with the existing rule that metadata writes
    must stay short-lived and StarRocks/S3 operations must not run inside an open transaction.

No code, schema, or API behavior changes in this change — it is a documentation-only update
that later changes (job event log, schedule/cluster delete cascades, schedule-scoped retention,
restore-by-job) will implement against.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
(none — this change touches `SPEC.md` and `AGENTS.md` only; no `openspec/specs/api-*`
capability's requirements change here. `skip_specs: true` is set in `.openspec.yaml` for this
reason. The API-level consequences of these decisions — e.g. `api-scheduling`'s delete endpoint
becoming asynchronous, `api-cluster-registry`'s delete guard covering disabled schedules — are
deferred to the changes in `PLAN.md` sections 8 and 12, which will each carry their own delta
specs when they change actual endpoint behavior.)

## Impact

- `SPEC.md`: §2 (domain model diagram), §8 (Manual Schedule), §10-11 (Retention), §22 (Failed
  Jobs and Retention), §25 (Restore Job), plus one new short section on cascading delete.
- `AGENTS.md`: "Parallel execution and metadata" section.
- `PLAN.md`: several sections were drafted against decisions that changed after the plan was
  written and are now inconsistent with it:
  - Decision 0.2 reverses back to allowing multi-database inventories; task 3.5 (reject a second
    database at add-table time) is removed, and multi-database backup execution (one `BACKUP
    DATABASE` per database under one Backup Job, several Backup References) returns as real
    implementation work.
  - Section 8 (schedule delete/expiry): reworded from a same-request cascade to synchronous
    validation (409 on an active job, or on a full backup that is another schedule's incremental
    baseline) followed by an async cleanup job; a new job type for that cleanup is added to the
    plan.
  - Section 12.1 (cluster delete): guard restated as "blocked while any schedule exists, enabled
    or disabled"; the cascade itself becomes the same async-job pattern as schedule delete
    instead of a same-request FK cascade.
  - Section 10 gets a note that `retention` is a documented domain entity (`SPEC.md` §2), not
    only an implementation choice.
- No source code, migrations, or API contracts change in this step. Downstream changes
  (`PLAN.md` sections 5, 6, 8, 9, 10, 11, 12, as updated above) will implement these rules against
  real schema, commands, and endpoints, each validated against the wording this change
  establishes.
