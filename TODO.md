# TODO — Gap analysis against SPEC.md

This file lists what is still missing, or needs to change, for the app to implement the domain
model in `SPEC.md` end to end. Per `AGENTS.md`, each item below should become its own OpenSpec
change (`openspec-propose`) before any implementation.

Items are grouped into phases. Each phase depends on the ones before it.

---

## What already exists (baseline)

| Spec entity / capability | Current implementation | Status |
|---|---|---|
| Cluster | `store.models.Cluster`, CRUD + verify routes, delete guard (`commands/clusters.py`) | Done |
| Repository | Live pass-through to StarRocks (`CREATE/DROP/SHOW REPOSITORY`), S3 verify, delete guard when snapshots exist | Done, but not persisted (see 1.3) |
| Inventory | `InventoryGroup` + `TableInventory`, `*` = whole database, CRUD routes, delete blocked by referencing schedules | Mostly done (see 1.4) |
| Schedule (recurring) | `store.models.Schedule`, cron `cadence`, CRUD, `POST /backup/schedules/run` with idempotent `next_run_at` advance | Partial: no automatic trigger, no retention, no one-shot |
| Backup Job | `store.models.Job` (types `backup_full`/`backup_incremental`/`restore`/`prune`), status `PENDING/RUNNING/SUCCESS/FAILED`, thread backend | Partial: not linked to a Schedule |
| Backup History (execution log) | `backup_history` table = **one summary row per StarRocks label**, not an append-only per-job event log | Not done |
| Backup Reference | `backup_partitions` (label + db/table/partition) and `Job.result_json["label"]` | Partial: not linked to the Job, not exposed |
| Retention | Only manual `prune` job scoped by inventory group (`keep_last`, `older_than`, ...) | Not done as specified |
| Restore Job | `restore` job type, submitted by `target_label` | Partial: no link to source Backup Job, same cluster only |
| Restore History | `restore_history` table = one summary row per restore | Not done as an event log |
| Backup history API | `GET /backup/history/cluster/{cluster_id}` with filters | Done |

---

## Phase 1 — Domain model and schema

### 1.1 Link Backup Jobs to Schedules
- Add `Job.schedule_id` (FK → `schedules.id`, nullable only for legacy/restore/prune jobs).
- Set it in `commands/schedules.run_due_schedules` and in any manual-backup path (see 2.1).
- Decide the FK behaviour when a schedule is deleted (spec: "a failed Job is not deleted";
  jobs are historical facts). Prefer `ON DELETE SET NULL` plus a denormalized snapshot of the
  schedule's retention/repository/inventory on the Job, or soft-delete schedules.
- Expose `schedule_id` in `JobRead` and add a `schedule_id` filter to `list_jobs` and
  `GET /backup/history/cluster/{cluster_id}`.

### 1.2 Store retention on the Schedule
- Add `Schedule.retention` (int ≥ 1, required) to the model, the baseline migration successor,
  `ScheduleCreate`/`ScheduleUpdate`/`ScheduleRead`, and `openspec/specs/api-scheduling/spec.md`.

### 1.3 Decide how Repository is modelled
- The spec treats Repository as a Cluster-owned entity referenced by Schedules. Today
  `Schedule.repository` is a free string validated live against `SHOW REPOSITORIES`.
- Either persist a `repositories` table (cluster_id, name, location, created_at; never the S3
  secrets) and make `Schedule.repository_id` a FK, **or** document explicitly that StarRocks is
  the source of truth and keep the string.
- In both cases: `DELETE /repositories/cluster/{id}/name/{name}` must refuse (409) while any
  schedule references the repository, as inventory-group delete already does.

### 1.4 Inventory spanning several databases
- The spec's inventory example includes Database A (some tables) **and** Database B (all tables)
  in one inventory. `planner.resolve_group_database` rejects this with
  `MultipleDatabasesInGroupError`.
- Either support multi-database inventories (one StarRocks `BACKUP DATABASE` per database, all
  recorded under one Backup Job with several Backup References), or change the spec to state
  "one database per inventory" and enforce it at inventory create/add-table time instead of at
  backup time.

### 1.5 Backup History as an append-only execution log
- Add a `backup_job_events` table (`id`, `job_id` FK, `ts`, `event`, `message`, `details_json`)
  — or rename/replace the current `backup_history` table — so one Job has 1:N entries.
- Event vocabulary per spec §17: `STARTED`, `CONNECTING`, `BACKUP_STARTED`, `DATABASE_STARTED`,
  `TABLE_COMPLETED`, `BACKUP_FINISHED`, `ERROR`, `SUCCESS`, `FAILED`
  (plus `RETENTION_STARTED`/`SNAPSHOT_DROPPED`/`RETENTION_FINISHED`, see 3.x).
- Writes must use short-lived sessions (append only, never update) so they are safe with
  parallel jobs.
- Keep the per-label summary (`backup_history`) only if still needed by the planner/restore
  lookups; otherwise derive it from Job + references.

### 1.6 Backup Reference as a first-class, Job-linked record
- Add `backup_references` (`job_id` FK, `repository`, `snapshot_label`, `database`, `table`,
  `partition` nullable, `snapshot_timestamp`) or add `job_id` to `backup_partitions`.
- Written only when the StarRocks backup reaches `FINISHED`; for a failed job, references may be
  recorded but must be flagged non-restorable (spec §16).

### 1.7 Restore Job linked to its source Backup Job
- Add `Job.source_job_id` (FK → `jobs.id`) and `Job.target_cluster_id` for restore jobs.
- Replace the free-form `restore_history.job_id` string with a FK to the restore Job.

### 1.8 Restore History as an append-only execution log
- Same shape as 1.5 (`restore_job_events`, or one shared `job_events` table with a job FK for
  every job type — simpler and recommended).
- Events per spec §26: `RESTORE_STARTED`, `CONNECTING`, `DATABASE_RESTORING`, `TABLE_RESTORED`,
  `RESTORE_FINISHED`, `ERROR`, `SUCCESS`, `FAILED`.

---

## Phase 2 — Schedules and job execution

### 2.1 Manual backup = one-shot Schedule (`cadence = null`)
- Make `Schedule.cadence` and `Schedule.next_run_at` nullable; `ScheduleCreate.cadence` optional.
- Creating a schedule with `cadence = null` persists it and immediately submits exactly one
  Backup Job through `commands.jobs.submit_job` (same path as recurring schedules).
- One-shot schedules are immutable: `PATCH` returns 409; `DELETE` is allowed.
- `run_due_schedules` must skip `cadence IS NULL` rows.
- Rework `POST /backup/manual/full|incremental/cluster/{cluster_id}` so they create a one-shot
  schedule internally (spec §8: "no separate execution mechanism for manual backups"), or
  deprecate them in favour of the schedule endpoint. Decide what `retention` a manual backup
  gets (required field on the request, or a documented default).
- Move schedule create/update/delete logic from `api/routes/schedules.py` into
  `commands/schedules.py` (AGENTS.md: routes call commands).

### 2.2 Automatic internal scheduler process
- Nothing calls `run_due_schedules` today except `POST /backup/schedules/run`.
- Add an in-process scheduler loop started from the FastAPI lifespan (configurable tick, e.g.
  `STARROCKS_BR_SCHEDULER_INTERVAL_SECONDS`, and a switch to disable it when an external cron
  hits `/backup/schedules/run` instead). Stop it cleanly on shutdown together with
  `ThreadBackend.shutdown`.
- The existing conditional-UPDATE idempotency must keep working with several API replicas.
- Decide the catch-up policy when the process was down for several cadence periods
  (current behaviour: run once, then jump to next future occurrence — document it).

### 2.3 Backup job lifecycle hardening
- Record `STARTED`/`CONNECTING`/... events (1.5) from `commands/backup.py` and forward
  `executor` progress callbacks into events, not only into `Job.state_detail`.
- On failure, log `ERROR` with the real message, then `FAILED`. Today a submit failure writes no
  `backup_history` row at all and leaves the `run_status` slot `ACTIVE` until stale-healing.
  Always release the concurrency slot in a `finally`.
- `executor.execute_backup` swallows history/slot write errors (`except Exception: pass`) —
  log them at least.
- Fix the gap noted in AGENTS.md: `commands/backup.py` keeps a session open while
  `executor.execute_backup` polls StarRocks. Split polling from metadata writes so every
  metadata write is a short transaction.
- Revisit `concurrency.reserve_job_slot` scope (`backup` per cluster serializes all backups on
  a cluster). Decide whether serialization should be per database/inventory instead, and make
  that policy explicit.

### 2.4 Recover jobs orphaned by a restart
- The thread backend is in-process: after a crash/restart, jobs stay `PENDING`/`RUNNING`
  forever and block `DELETE /cluster/{id}` (active-job guard).
- On startup, reconcile: re-enqueue `PENDING` jobs; for `RUNNING` jobs, check StarRocks
  (`SHOW BACKUP`/`SHOW RESTORE` by label) and mark `SUCCESS`/`FAILED` accordingly, appending
  a history event explaining the reconciliation.

---

## Phase 3 — Retention

### 3.1 Automatic per-schedule retention after a successful backup
- After a Backup Job of a schedule reaches `SUCCESS`, run retention for **that schedule only**
  (spec §11, §21): take `SUCCESS` jobs with the same `schedule_id`, newest first, keep
  `schedule.retention`, drop the rest.
- Ignore `FAILED` jobs entirely (spec §22 example must be a unit test).
- Deletion = `DROP SNAPSHOT` in the job's repository for each reference; mark the job's
  references as deleted (do **not** delete the Job or its history — the job remains a
  historical fact; add a `Job.data_deleted_at` / reference `deleted_at` column).
- Record retention events in the triggering job's log (or as a separate `retention` job with
  its own log — pick one).
- A retention failure must not turn a successful backup into `FAILED`; log it and surface it.

### 3.2 Current `prune` vs. spec retention
- Current prune selects backups by **inventory group** (joining `backup_partitions` to
  `table_inventory`), so two schedules on the same inventory share one pool — this contradicts
  spec §11. Either re-scope manual prune to a schedule/job list or keep it as an
  admin-only tool clearly separate from retention.
- `prune.cleanup_backup_history` currently deletes history rows; with 1.5 this must become a
  "data deleted" marker instead, because history is append-only.

### 3.3 Incremental backups and retention
- `SPEC.md` does not mention incremental backups, but the app supports them. Dropping an old
  full backup can break the chain of incrementals that depend on it. Decide and specify:
  retention counts full+incremental together, keeps the baseline full of any retained
  incremental, or schedules are full-only. Add `baseline_job_id` to incremental jobs so the
  dependency is explicit.

---

## Phase 4 — Restore

### 4.1 Restore from a Backup Job, not a label
- Restore request takes `source_job_id` (a successful backup job) instead of — or in addition
  to — `target_label`. Validate synchronously: job exists, is a backup job, `status = SUCCESS`,
  its data has not been deleted by retention (spec §24), else 404/409.
- Resolve repository/snapshot/tables from the job's Backup References (1.6) instead of
  `backup_history` by label.

### 4.2 Target cluster
- Spec §25: a Restore Job references `source_backup_job` **and** `target_cluster`. Today restore
  always runs on the cluster the backup came from.
- Support restoring into another registered cluster: the target cluster must have a repository
  pointing to the same location (create it read-only if needed), then `RESTORE SNAPSHOT` there.

### 4.3 Restore must not modify the Backup Job
- Ensure the restore flow writes nothing to the source job, its history, or its references
  (spec §27); add a test for it.

### 4.4 Restore job API
- `GET /restore/history/cluster/{cluster_id}` (list restore jobs, filter by `source_job_id`,
  `status`), and `GET /backup/job/{job_id}/restores` for the 1:N view from a backup job.
- `restore` submission should live under the `/restore/...` domain per the API path convention
  (`/{domain}/{operation}/{context}/{ids}`), e.g. `POST /restore/manual/cluster/{cluster_id}`;
  keep the old route as an alias during migration if needed.

---

## Phase 5 — API surface gaps

- `JobRead` does not expose `result_json` (the produced snapshot label), `group_id`,
  `schedule_id`, or references — a client cannot find what to restore from without reading the
  DB (the pending integration-test change works around this with a DB helper). Add them.
- `GET /job/{job_id}/history` (or `/backup/history/.../job_id/{job_id}/log`) returning the
  append-only event log of 1.5/1.8.
- `GET /job/{job_id}/references` returning Backup References.
- `GET /backup/history/cluster/{cluster_id}` should accept `schedule_id` and
  `restorable=true` (SUCCESS and data not deleted) filters.
- Schedule read model: add `retention`, `last_success_job_id`, and next/last run info.
- Update the matching specs in `openspec/specs/api-*/spec.md` for every endpoint change.

---

## Phase 6 — Consistency and integrity rules

- Schedule same-cluster rule (spec §6): inventory and repository must belong to the schedule's
  cluster. Inventory is already checked via `group_exists(cluster_id, ...)`; once repositories
  are persisted (1.3) check the FK too, on both create and update.
- Cluster delete: `jobs.cluster_id` / `schedules.cluster_id` have no `ON DELETE` policy. Decide
  whether deleting a cluster is blocked while jobs/schedules exist, or cascades; today a
  cluster with only disabled schedules or finished jobs can fail on FK constraints (verify with
  SQLite `PRAGMA foreign_keys=ON` and with Postgres/MySQL).
- Schedule delete: decide what happens to its jobs and the backup data in the repository
  (keep as orphaned-but-restorable, or require retention cleanup first).
- Move inventory-group routes and pre-validation behind `commands/` (AGENTS.md known
  exception), keeping behaviour.

---

## Phase 7 — Tests and operations

- Finish the open change `openspec/changes/add-scheduled-backup-restore-integration-test`
  (all tasks unchecked).
- Unit tests (`tests/unit/service/`): one-shot schedule immutability, scheduler loop tick,
  retention per schedule incl. the §22 example, failed jobs ignored, restore rejects failed /
  pruned jobs, restore does not touch the source job, startup job reconciliation.
- Integration tests (`tests/integration/`): recurring schedule → N successful jobs → retention
  drops the oldest snapshot from S3; restore from a non-latest job; restore into a second
  cluster (if 4.2 is implemented).
- Operations: document running the server (env vars, scheduler toggle, DB URL, migrations with
  `alembic upgrade head` on startup or as a separate step), and a health endpoint that reports
  scheduler liveness.

---

## Suggested order of OpenSpec changes

1. `add-job-event-log` (1.5, 1.8, 2.3 events, 5 history endpoint)
2. `link-jobs-to-schedules-and-retention-field` (1.1, 1.2)
3. `add-backup-references` (1.6, 5 references/result exposure)
4. `one-shot-schedules` (2.1)
5. `add-internal-scheduler` (2.2, 2.4)
6. `schedule-scoped-retention` (3.1–3.3)
7. `restore-from-backup-job` (1.7, 4.1–4.4)
8. `persist-repositories` and `multi-database-inventory` (1.3, 1.4) — decide early, since they
   change schedule and reference shapes
9. Integrity/cleanup and tests (6, 7)
