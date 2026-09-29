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

## 0. Architectural Decisions

- [x] **0.1 Repository resolution**: Repositories are not persisted in metadata; StarRocks is the source of truth, referenced as `(cluster_id, name)` and validated live via `SHOW REPOSITORIES`.
- [x] **0.2 Multi-database inventories**: Inventories can span multiple databases; execution runs one `BACKUP DATABASE` per database under a single Backup Job.
- [x] **0.3 Schedule deletion**: Validated synchronously (409 on active jobs or baseline dependencies), then executed asynchronously via a `schedule_cleanup` job that drops S3 snapshots and cascades metadata deletions.
- [x] **0.4 Manual backups as one-shot schedules**: Manual backups use `cadence = null` with optional `expire_after_days`; on expiry, they clean up via the same async cascade.
- [x] **0.5 Incremental backup protection**: Incrementals have no retention policy; full baselines with dependent incrementals are strictly protected from deletion or retention pruning.
- [x] **0.6 Retention jobs**: Retention runs as an independent `retention` job sharing the cluster `backup` concurrency scope.
- [x] **0.7 Cross-cluster restore**: Restoring backups into different clusters is supported.
- [x] **0.8 Cluster deletion**: Blocked (409) if any schedule exists; otherwise cascades asynchronously via `cluster_cleanup`.
- [x] **0.9 Concurrency**: Strictly one backup operation per cluster at a time (`backup` scope in `concurrency.reserve_job_slot`).

### Follow-up Decisions

- [x] **Q1 Retention scope**: Retention applies only to full backups; baselines of existing incrementals are never dropped.
- [x] **Q2 Deletion/expiry baseline guard**: Schedule delete (409) and one-shot expiry (retry later) are blocked if dropping a full backup needed by an existing incremental.
- [x] **Q3 One-shot constraints**: One-shot schedules must be full backups only; one-shot incremental requests are rejected (422).
- [x] **Q4 Retention model**: Recurring schedules use count-based retention only.
- [x] **Q5 Schedule delete active job guard**: Returns 409 if any schedule job is `PENDING` or `RUNNING`.
- [x] **Q6 Restore job cascade**: Deleting a backup job cascades to its dependent restore jobs (`ON DELETE CASCADE`).
- [x] **Q7 Cluster delete restore cascade**: Cluster deletion cascades to restore jobs where the cluster is source or target.
- [x] **Q8 Snapshot removal**: Schedule deletion always drops repository/S3 snapshots.
- [x] **Q9 Retention field validation**: `Schedule.retention` is nullable in DB but validated as required on creation for recurring full schedules.
- [x] **Q10 Schedule ID nullability**: `Job.schedule_id` is nullable.
- [x] **Q11 Breaking change**: Requiring `retention` on recurring full schedules is a documented breaking change on `ScheduleCreate`.
- [x] **Q12 Baseline job resolution**: `Job.baseline_job_id` is resolved and populated during incremental planning and execution.
- [x] **Q13 Scheduler execution modes**: Periodic runs are supported both via CLI (`starrocks-br-scheduler tick`) and HTTP (`POST /backup/schedules/run`).

## 1. Baseline Implementation
- [x] 1.1 Cluster model, CRUD endpoints, credential encryption, live verification, and delete guard (`commands/clusters.py`).
- [x] 1.2 Live repository management via StarRocks (`CREATE`/`DROP`/`SHOW REPOSITORY`) without metadata persistence.
- [x] 1.3 Inventory group and table inventory CRUD with whole-database wildcard (`*`) support.
- [x] 1.4 Recurring schedules with cron expressions, inventory validation, and `POST /backup/schedules/run` execution.
- [x] 1.5 Job engine supporting core job types, lifecycle states, thread backend, and status queries.
- [x] 1.6 Core manual full/incremental backup, restore by label, and baseline verification tests.
- [x] 1.7 Cluster backup history querying with filtering and pagination.
- [x] 1.8 Alembic baseline migrations with SQLite default and `STARROCKS_BR_DATABASE_URL` configuration.
- [x] 1.9 API key authentication, health check endpoint, and commands layer coordination.
- [x] 1.10 Cluster backup concurrency serialization via `concurrency.reserve_job_slot`.

## 2. Specification Updates
- [x] 2.1 Updated `SPEC.md` for Retention Jobs, one-shot expiry, incremental baseline protection, async deletion cascades, and restore job cascades.
- [x] 2.2 Updated `AGENTS.md` concurrency guidelines for cluster backup serialization and short metadata transaction lifetimes.

## 3. Repository Reference Hardening
- [x] 3.1 Enforced live validation of `(cluster_id, repository_name)` against StarRocks `SHOW REPOSITORIES` on schedule create/update.
- [x] 3.2 Blocked cross-cluster repository references.
- [x] 3.3 Documented repository reference semantics in `SPEC.md` §4 and verified with tests.

## 4. Job History Log
- [x] 4.1 Migrated `backup_history` and `restore_history` to append-only event logs keyed by `job_id` (FK `ON DELETE CASCADE`) with `ts`, `status`, `message`, and `details_json`.
- [x] 4.2 Implemented `append_backup_event` and `append_restore_event` with state-change deduplication and guaranteed terminal event logging in short-lived sessions.
- [x] 4.3 Wired StarRocks native poll states (`SHOW BACKUP` / `SHOW RESTORE`) and terminal outcomes into the history loggers.
- [x] 4.4 Added `GET /job/{job_id}/history` endpoint returning chronological event rows.
- [x] 4.5 Verified append-only behavior, state deduplication, and cascading deletions with unit tests.

## 5. Schedules, Retention/Expiry Fields, and One-Shot Schedules
- [x] 5.1 Made `Schedule.cadence` and `next_run_at` nullable (`cadence = null` marks one-shot schedules).
- [x] 5.2 Added `retention` and `expire_after_days` to `Schedule`; rejected one-shot incremental schedules with 422.
- [x] 5.3 Implemented immediate job submission upon one-shot schedule creation.
- [x] 5.4 Blocked modification (PATCH → 409) of one-shot schedules and excluded them from `run_due_schedules`.
- [x] 5.5 Added `Job.schedule_id` and populated `Job.baseline_job_id` during incremental execution.
- [x] 5.6 Exposed `schedule_id`, `group_id`, `baseline_job_id`, and `result_json` in `JobRead`; added `schedule_id` query filters.
- [x] 5.7 Verified one-shot execution, immutability, and field validations with unit and service tests.

## 6. Backup References and Multi-Database Support
- [x] 6.1 Added `backup_references` table (`job_id` FK `ON DELETE CASCADE`, `repository`, `snapshot_label`, `database`, `table`, `partition`, `snapshot_timestamp`, `deleted_at`).
- [x] 6.2 Recorded backup references upon successful backup completion (`FINISHED`).
- [x] 6.3 Added `GET /job/{job_id}/references` endpoint.
- [x] 6.4 Implemented multi-database inventory backups (executing `BACKUP DATABASE` per database under a single Backup Job).
- [x] 6.5 Verified multi-database references and restorable reference queries with tests.

## 7. Backup Job Lifecycle Hardening
- [x] 7.1 Decoupled StarRocks polling from metadata transactions so no database session remains open during polling.
- [x] 7.2 Guaranteed concurrency slot release in `finally` blocks and recorded explicit `ERROR`/`FAILED` history events on submission failure.
- [x] 7.3 Replaced silent exception suppression with structured error logging.
- [x] 7.4 Verified slot release and failure event recording with tests.

## 8. Manual Route Retirement and Schedule Deletion Cascade
- [x] 8.1 Retired `/backup/manual/full` and `/backup/manual/incremental` routes in favor of one-shot and recurring schedules.
- [x] 8.2 Added async `schedule_cleanup` job type to drop StarRocks snapshots via references and cascade-delete metadata.
- [x] 8.3 Implemented synchronous guards on schedule deletion (409 on active jobs or baseline dependencies), returning `202 Accepted` with cleanup job ID.
- [x] 8.4 Implemented automatic expiry for one-shot schedules past `expire_after_days` via `schedule_cleanup`.
- [x] 8.5 Verified 202 deletion cascade, snapshot removal, dependency guards, and expiry execution with tests.

## 9. Scheduler CLI Command, Concurrency, and Recovery
- [x] 9.1 Scoped scheduler execution to single-tick invocations via CLI command (`starrocks-br-scheduler tick`) alongside `POST /backup/schedules/run`.
- [x] 9.2 Implemented `src/starrocks_br/cli/scheduler.py` calling `commands.schedules.run_due_schedules` and one-shot expiry without in-process sleep loops.
- [x] 9.3 Added cluster-wide singleton `scheduler_lock` in metadata store with atomic acquisition, host:pid tracking, and timeout expiration.
- [x] 9.4 Handled lock contention (clean non-zero exit) and stale lock recovery with warning logs.
- [x] 9.5 Implemented orphan job reconciliation on each tick (`PENDING` re-enqueued, `RUNNING` checked against StarRocks status).
- [x] 9.6 Recorded `last_tick_at` timestamp and exposed scheduler status via `/health`.
- [x] 9.7 Verified single-instance lock concurrency, stale lock recovery, due run triggering, and reconciliation with tests.

## 10. Schedule-Scoped Retention
- [x] 10.1 Added `retention` job type and `RetentionHistory` append-only event log sharing the `backup` concurrency scope.
- [x] 10.2 Implemented candidate selection keeping latest N full backups and strictly protecting incremental baselines and active restore sources.
- [x] 10.3 Dropped snapshots via StarRocks and marked references `deleted_at`.
- [x] 10.4 Integrated retention sweep into scheduler tick and added retention cleanup to `schedule_cleanup`.
- [x] 10.5 Retired manual prune route (`/backup/manual/prune/...`).
- [x] 10.6 Verified candidate selection, baseline protection, and failure isolation with tests.

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
