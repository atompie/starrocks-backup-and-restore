# PLAN — StarRocks Backup System, path to SPEC.md

Context: `SPEC.md` describes the target domain model. This plan
merges both with the current code into one ordered tick list. Ticked items were checked against
the code, and the test suite passes on 2026-09-27: about 582 unit tests and 22 integration tests
against a live StarRocks and S3 stack, with 0 failures and 0 skips.
Per AGENTS.md, each numbered section (2 onward) becomes one OpenSpec change (`openspec-propose`)
before it is implemented, and it is archived once done.

Order rationale: record decisions in the spec first, then schema, then execution, then
retention/expiry and restore, then API polish, integrity and end-to-end tests. Each section
builds only on earlier ones.

## 0. Decisions (answered 2026-09-27)

- [x] 0.1 ~~Repository: persist a `repositories` table (cluster-owned); `Schedule.repository_id` is an FK. Repository delete is blocked while schedules reference it.~~ — **reversed 2026-09-27**: StarRocks itself is the single source of truth for repositories. Repository is never persisted in the metadata DB; it is referenced everywhere as `(cluster_id, name)` and validated live against `SHOW REPOSITORIES`, matching the existing pass-through implementation. Uniqueness of `(cluster, name)` is enforced by StarRocks itself (`CREATE REPOSITORY` rejects a duplicate name on that cluster), so no local uniqueness constraint is needed.
- [x] 0.2 ~~Inventory: one database per inventory~~ — **reversed 2026-09-27**: an inventory may span multiple databases, as `SPEC.md` §5 originally showed. Multi-database backup execution (one `BACKUP DATABASE` per database under one Backup Job, several Backup References) is real implementation work — see section 3.
- [x] 0.3 Schedule delete: validated synchronously (409 on an active job, or on a dependent-incremental baseline — see Q2/Q5), then accepted and run as an async cleanup job that deletes the schedule's jobs, their events and references, and drops their snapshots from the repository (S3).
- [x] 0.4 Manual backup = one-shot schedule (`cadence = null`) with an expiry policy defined on the schedule: never, or after N days. When it expires, it is deleted like 0.3.
- [x] 0.5 Incremental backups have no retention and are never removed by retention. The full backups they depend on must be kept too.
- [x] 0.6 Retention runs as its own job (`job_type = retention`) with its own event log — a documented domain entity in `SPEC.md` §2 (peer of Backup Job / Restore Job), not just an implementation choice. It shares the `backup` concurrency scope, so it can never run concurrently with a backup on the same cluster.
- [x] 0.7 Restore into a different cluster is in scope.
- [x] 0.8 Cluster delete is blocked (409) while the cluster has **any** schedule, enabled or disabled. With none, deletion cascades the same way schedule delete does (0.3): synchronous validation, then an async cleanup job.
- [x] 0.9 Keep one backup at a time per cluster (current `backup` scope in `concurrency.reserve_job_slot`).

## 0b. Follow-up decisions (answered 2026-09-27)

- [x] Q1 Retention applies to full backups only. A full backup that is the baseline of any existing incremental is never dropped by retention, even beyond `retention`.
- [x] Q2 If deleting a schedule or expiring a one-shot would drop a full backup that an existing incremental (any schedule) depends on, it is blocked: delete → 409; expiry → skipped, logged, retried on the next tick.
- [x] Q3 One-shot schedules are full backups only; a one-shot incremental is rejected (422). Incrementals come only from recurring schedules.
- [x] Q4 Recurring schedules use count-based retention only.
- [x] Q5 Schedule delete → 409 while any of its jobs is `PENDING`/`RUNNING`.
- [x] Q6 Deleting a backup job also deletes the restore jobs that used it (FK `ON DELETE CASCADE`).
- [x] Q7 Cluster delete (no schedules, enabled or disabled) is allowed and, as part of the same async cleanup job (0.8), deletes its restore jobs, both as source and as target. Restore jobs are only a record of occasional restores.
- [x] Q8 Schedule delete always drops the S3 snapshots; no confirmation flag.

## 1. Baseline — already done and verified

- [x] 1.1 Cluster: model, CRUD, verify endpoints, encrypted password, delete guard (`commands/clusters.py`; integration `test_clusters_live.py`)
- [x] 1.2 Repository: create/list/delete via StarRocks `CREATE/DROP/SHOW REPOSITORY`, S3 verify, delete guard when snapshots exist (integration `test_repositories_live.py`). Not persisted in the metadata DB by design — StarRocks is the source of truth (see §3, 0.1).
- [x] 1.3 Inventory: `InventoryGroup` + `TableInventory`, `*` = whole database, CRUD routes, delete blocked by referencing schedules (integration `test_inventory_live.py`)
- [x] 1.4 Recurring Schedule: cron `cadence`, CRUD under `/backup/schedules/cluster/{id}`, inventory checked against the same cluster, `POST /backup/schedules/run` with idempotent conditional `next_run_at` advance (`commands/schedules.py`)
- [x] 1.5 Job model with types `backup_full/backup_incremental/restore/prune` and status `PENDING/RUNNING/SUCCESS/FAILED`, thread backend, `GET /job/{id}`
- [x] 1.6 Manual full/incremental backup, restore by label (full, or base full + incremental), prune by group via `/backup/manual/...`. Full backup → drop DB → restore passes live (`test_full_backup_restore_cycle.py`).
- [x] 1.7 `GET /backup/history/cluster/{id}` with `job_type/status/job_id/group_id/limit/offset` filters
- [x] 1.8 Single flattened Alembic baseline migration, SQLite default, `STARROCKS_BR_DATABASE_URL`
- [x] 1.9 API key auth, health endpoint, commands layer for backup/restore/prune/jobs/schedules
- [x] 1.10 One backup at a time per cluster (`concurrency.reserve_job_slot`, scope `backup`), matching decision 0.9

## 2. Record the decisions in the spec

OpenSpec change: `record-lifecycle-decisions-in-domain-spec` (docs-only, `skip_specs: true`).

- [x] 2.1 Update `SPEC.md`: add `Retention Job` to the §2 domain model as a peer of Backup Job/Restore Job (own id, status, append-only History); one-shot expiry (never / N days) in §8; incremental backups and the full backups they depend on are exempt from retention (§10-11, §22); a new section describing schedule delete and cluster delete as synchronously-validated, asynchronously-executed cascades (409 up front on an active job or a dependent-incremental baseline; otherwise a cleanup job drops S3 data and deletes jobs/events/references); a note that a Restore Job is deleted along with its source Backup Job (§27, was §25 before the two new sections shifted later numbering). Multi-database inventories are *not* changed in `SPEC.md` (0.2 reversed — §5's existing example already allows them).
- [x] 2.2 Update `AGENTS.md`'s "Parallel execution and metadata" paragraph: backup serialization per cluster is the confirmed policy (not a gap); retention shares the `backup` scope; schedule/cluster delete follow the same synchronous-accept-then-async-job pattern as backup submission.
- [x] 2.3 No `openspec/specs/api-*` changes in this section — those land alongside the endpoint behavior changes in sections 8 and 12.

## 3. Repository reference hardening

Repository is not a persisted entity in this system's metadata store — StarRocks is the single
source of truth (0.1, reversed 2026-09-27). This section formalizes and hardens the existing
live-validation pattern rather than adding schema.

- [ ] 3.1 Confirm `Schedule.repository` (string) stays as-is; both create and update validate it live against `SHOW REPOSITORIES` on the schedule's cluster (already implemented via `ensure_repository_exists`)
- [ ] 3.2 Add a same-cluster cross-check test: a schedule cannot reference a repository name that only exists on a different cluster
- [ ] 3.3 Document the `(cluster_id, name)` reference pattern in `SPEC.md` §4, and note it applies wherever a repository is referenced (Schedule, Backup Reference, restore target)
- [ ] 3.4 Tests: repository validated live on schedule create/update; cross-cluster repository name rejected

## 4. Job event log (Backup, Restore and Retention History)

- [ ] 4.1 Add a `job_events` table (`id`, `job_id` FK `ON DELETE CASCADE`, `ts`, `event`, `message`, `details_json`) for all job types, with a migration
- [ ] 4.2 Add a `history.append_event(job_id, event, ...)` helper that runs in its own short-lived session and only ever inserts
- [ ] 4.3 Emit backup events (§17): `STARTED`, `CONNECTING`, `BACKUP_STARTED`, `DATABASE_STARTED`, `TABLE_COMPLETED`, `BACKUP_FINISHED`, `SUCCESS` / `ERROR`, `FAILED`. Forward `executor` progress callbacks into events.
- [ ] 4.4 Emit restore events (§26): `RESTORE_STARTED`, `CONNECTING`, `DATABASE_RESTORING`, `TABLE_RESTORED`, `RESTORE_FINISHED`, `SUCCESS` / `ERROR`, `FAILED`
- [ ] 4.5 Add `GET /job/{job_id}/history` that returns the event log in time order
- [ ] 4.6 Unit tests: success path and failure path event sequences; confirm events are never updated

## 5. Link jobs to schedules; retention and expiry fields

- [ ] 5.1 Add to `Schedule`: `retention` (int ≥ 1; required for recurring full, must be null for incremental), and `expire_after_days` (one-shot only; null = never). Reject a one-shot incremental schedule (Q3). Update model, migration, `ScheduleCreate/Update/Read` and `openspec/specs/api-scheduling`.
- [ ] 5.2 Add `Job.schedule_id` (FK `ON DELETE CASCADE`, nullable only for restore jobs) and set it in `run_due_schedules`
- [ ] 5.3 Add `Job.baseline_job_id` for incremental jobs (the full job it depends on), set from the planner's baseline lookup
- [ ] 5.4 Expose `schedule_id`, `group_id`, `baseline_job_id` and `result_json` in `JobRead`; add a `schedule_id` filter to `list_jobs` and `/backup/history/cluster/{id}`
- [ ] 5.5 Move schedule create/update/delete logic from `api/routes/schedules.py` into `commands/schedules.py`
- [ ] 5.6 Tests: a job created by run-due carries `schedule_id`; field validation per job type and cadence

## 6. Backup References

- [ ] 6.1 Add a `backup_references` table (`job_id` FK `ON DELETE CASCADE`, `repository` (string name; cluster resolved via `job.cluster_id` — 0.1), `snapshot_label`, `database`, `table`, `partition` nullable, `snapshot_timestamp`, `deleted_at` nullable). Migrate `backup_partitions` into it or add `job_id` to it.
- [ ] 6.2 Write references when the StarRocks backup reaches `FINISHED`; a failed job's references are never restorable (§16)
- [ ] 6.3 Add `GET /job/{job_id}/references`
- [ ] 6.4 Tests: a successful job has references; a failed job has none that are restorable
- [ ] 6.5 Multi-database inventory backups (0.2 reversed): replace `planner.resolve_group_database`'s `MultipleDatabasesInGroupError` rejection with support for one `BACKUP DATABASE` per database in the group, all recorded under one Backup Job with references per database/table
- [ ] 6.6 Tests: a backup job spanning two databases produces references for both and restores correctly

## 7. Backup job lifecycle hardening

- [ ] 7.1 Split StarRocks polling from metadata writes in `commands/backup.py` / `executor.execute_backup` so no session stays open during polling
- [ ] 7.2 Always release the concurrency slot in `finally`; on submit failure, record `ERROR` + `FAILED` with the real message
- [ ] 7.3 Replace `except Exception: pass` in the executor with logged errors
- [ ] 7.4 Tests: slot is released on failure; failure produces `ERROR` then `FAILED`

## 8. One-shot schedules, expiry and schedule delete cascade

- [ ] 8.1 Make `Schedule.cadence` and `next_run_at` nullable (migration + schemas)
- [ ] 8.2 Creating a schedule with `cadence = null` persists it and immediately submits exactly one job through `commands.jobs.submit_job`
- [ ] 8.3 PATCH on a one-shot schedule → 409; DELETE → allowed
- [ ] 8.4 `run_due_schedules` skips `cadence IS NULL`
- [ ] 8.5 Replace `/backup/manual/full` with one-shot schedule creation (thin wrapper, or removal); remove `/backup/manual/incremental` (Q3); update the `api-job-execution` spec
- [ ] 8.6 Add job type `schedule_cleanup`: given a schedule id, `DROP SNAPSHOT` for every reference (Q8), then delete the schedule's jobs; events, references and dependent restore jobs cascade (Q6); finally delete the schedule row
- [ ] 8.7 Schedule delete (`commands/schedules.delete_schedule` / `DELETE /backup/schedules/.../{id}`): validate synchronously — 409 if any job is `PENDING`/`RUNNING` (Q5); 409 if any of its full jobs is the `baseline_job_id` of an existing incremental in another schedule (Q2) — then submit a `schedule_cleanup` job and respond `202` with the job id (a **BREAKING** change to today's `204` contract; update `api-scheduling`)
- [ ] 8.8 Expiry: one-shot schedules past `created_at + expire_after_days` go through the same synchronous checks and `schedule_cleanup` job as 8.7, run from the scheduler tick in section 9. If blocked (Q2/Q5), skip, log a warning and retry on the next tick.
- [ ] 8.9 Tests: immutability, single job submitted, one-shot incremental rejected, skipped by run-due, delete returns 202 and the cleanup job drops snapshots and rows, delete blocked by active job or dependent incremental, expiry triggers cleanup, blocked expiry retried, never-expiring schedules kept

## 9. Internal scheduler process and restart recovery

- [ ] 9.1 Start a scheduler loop from the FastAPI lifespan that, on each tick every `STARROCKS_BR_SCHEDULER_INTERVAL_SECONDS`, runs `run_due_schedules` and one-shot expiry; add a switch to disable it; stop it cleanly together with `ThreadBackend.shutdown`
- [ ] 9.2 Document the catch-up policy: after downtime, run once, then jump to the next future occurrence
- [ ] 9.3 On startup, reconcile orphaned jobs: re-enqueue `PENDING`; for `RUNNING`, check `SHOW BACKUP/RESTORE` by label → `SUCCESS`/`FAILED`, and append a reconciliation event
- [ ] 9.4 Report scheduler liveness in `/health`
- [ ] 9.5 Tests: a scheduler tick triggers due schedules and expiry; reconciliation outcomes

## 10. Schedule-scoped retention (own job)

- [ ] 10.1 Add job type `retention` (now a documented domain entity, `SPEC.md` §2 — a peer of Backup Job/Restore Job, not just an implementation detail). When a recurring full-backup job succeeds, submit a `retention` job for that schedule, reserved under the same `backup` concurrency scope (0.6); it logs its own events (`RETENTION_STARTED`, `SNAPSHOT_DROPPED`, `RETENTION_FINISHED`, `ERROR`, `FAILED`).
- [ ] 10.2 Selection: that schedule's `SUCCESS` full jobs whose data is not deleted, newest first; keep `schedule.retention`; skip any job that is `baseline_job_id` of an existing incremental (Q1); drop the rest
- [ ] 10.3 Deletion = `DROP SNAPSHOT` per reference and set `deleted_at`. The backup Job and its events are kept.
- [ ] 10.4 A retention failure never changes the backup job's status
- [ ] 10.5 Retire the inventory-scoped `prune` job and its route (it conflicts with spec §11), or keep it admin-only; `prune.cleanup_backup_history` must stop deleting history rows
- [ ] 10.6 Tests: the §22 example (only Job 1 dropped), failed jobs ignored, incrementals and their baselines never dropped, two schedules on the same inventory keep independent pools

## 11. Restore from a Backup Job, into any cluster

- [ ] 11.1 Add `Job.source_job_id` (FK `ON DELETE CASCADE`, Q6) and `Job.target_cluster_id` for restore jobs; drop `restore_history` in favour of `job_events`
- [ ] 11.2 Restore request takes `source_job_id` and optional `target_cluster_id` (default: source cluster). Validate synchronously: exists, is a backup, `SUCCESS`, data not deleted → otherwise 404/409.
- [ ] 11.3 Resolve the chain from references: full → `[full]`; incremental → `[baseline full, incremental]` using `baseline_job_id` (replaces the label lookup in `restore.py`)
- [ ] 11.4 Cross-cluster: ensure the target cluster has a repository at the same location (register it read-only if missing), then `RESTORE SNAPSHOT` on the target; take the concurrency slot on the target cluster
- [ ] 11.5 Routes: `POST /restore/manual/cluster/{cluster_id}`, `GET /restore/history/cluster/{cluster_id}` (filters `source_job_id`, `status`), `GET /backup/job/{job_id}/restores`; retire `/backup/manual/restore/...`
- [ ] 11.6 Tests: failed or deleted source rejected; restore writes nothing to the source job, events or references (§27); restore of a non-latest job; incremental chain resolution; cross-cluster repository setup

## 12. Consistency and integrity

- [ ] 12.1 Add job type `cluster_cleanup`: given a cluster id, delete its inventories, remaining jobs (and their events/references, dropping S3 snapshots), and restore jobs where it is source or target (Q7); finally delete the cluster row. Repositories the system created on that cluster are **not** deleted or unregistered — they remain in StarRocks untouched (0.1, `SPEC.md` §24); cleanup never calls `DROP REPOSITORY`.
- [ ] 12.2 Cluster delete (`commands/clusters.delete_cluster` / `DELETE /cluster/{id}`): 409 while the cluster has **any** schedule, enabled or disabled (0.8); otherwise submit a `cluster_cleanup` job and respond `202` with the job id (a **BREAKING** change to today's synchronous delete; update `api-cluster-registry`). Enable SQLite `PRAGMA foreign_keys=ON` so per-row cascades inside the cleanup job behave the same on SQLite and Postgres/MySQL, and test it.
- [ ] 12.3 Move inventory-group routes and pre-validation behind `commands/`, keeping behaviour
- [ ] 12.4 Add a `/backup/history` `restorable=true` filter; add `last_success_job_id` and last-run info to the schedule read model

## 13. End-to-end tests, specs, docs

- [ ] 13.1 Finish the open change `add-scheduled-backup-restore-integration-test`, adapted to `source_job_id` restore
- [ ] 13.2 Integration: recurring schedule → N successes → retention job drops the oldest snapshot from S3; one-shot expiry; schedule delete removes S3 data; restore from a non-latest job; restore into a second cluster
- [ ] 13.3 Sync every `openspec/specs/api-*/spec.md` and archive each change
- [ ] 13.4 Update `docs/` (scheduling, API, configuration: scheduler env vars, migrations on startup); 

## Verification (after each section)

- `python -m pytest tests/unit` (all green)
- `python -m pytest tests/integration` against the local StarRocks/MinIO stack
- Final check: create cluster → repository → inventory → recurring full schedule with retention 2 → let the scheduler run 3 times → confirm 3 backup jobs + retention jobs, oldest snapshot dropped, event logs present → one-shot backup with 1-day expiry → restore the middle job into a second cluster → confirm data restored and the source job unchanged → delete the schedule → confirm `202` and a `schedule_cleanup` job → wait for it to reach `SUCCESS` → snapshots gone from S3.
