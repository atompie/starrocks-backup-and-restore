# Exploration: Section 10 — Schedule-Scoped Retention (Own Job)

A deep exploration of `SPEC.md` (§2, §10, §11, §21-23), `PLAN.md` (§0.5, §0.6, §Q1, §Q4, §10), and the codebase architecture for implementing schedule-scoped retention as an independent, automated job.

---

## 1. Executive Summary & Problem Space

In the domain specification, retention is not a manual operator task or an inventory-wide prune; it is an **automated, schedule-scoped lifecycle operation** modeled as a first-class peer of Backup and Restore jobs:

1. **Independent Retention Pools (`SPEC.md` §11)**: Retention operates strictly per schedule (`Schedule.retention`). Two schedules targeting the exact same inventory group and repository maintain separate retention pools.
2. **Metadata Preservation (`SPEC.md` §13, §14)**: Dropping old backups deletes data from StarRocks/S3 and marks `BackupReference.deleted_at = now`, but **never deletes `Job` rows or their `backup_history`**.
3. **Incremental Baseline Protection (`SPEC.md` §21, §22, Decision Q1)**: A full backup serving as the baseline for an existing incremental backup is protected from deletion indefinitely, regardless of retention limits.
4. **Failure Isolation (`SPEC.md` §23, `PLAN.md` §10.4)**: Retention runs as an independent `retention` job. Any failure during retention (concurrency conflict, StarRocks timeout, S3 failure) marks the retention job `FAILED`, but **never modifies the status of the completed backup job** that triggered it.
5. **Retirement of Legacy Prune**: The legacy `POST /backup/manual/prune/cluster/{cluster_id}` endpoint groups backups across schedules by inventory group and hard-deletes `Job` rows, directly violating §11 and §13-14. It is retired.

---

## 2. Architecture & Interaction Topology

```text
+--------------------------------------------------------------------------+
|                              CLUSTER                                     |
|                                                                          |
|   +------------------------------------------------------------------+   |
|   |                            SCHEDULE                              |   |
|   |   cadence: "0 2 * * *" (recurring)                               |   |
|   |   retention: 5 (copies of successful full backups)               |   |
|   +---------------------------------+--------------------------------+   |
|                                     |                                    |
|                   1. triggers       | 3. on SUCCESS                      |
|                      due backup     |    commits status &                |
|                                     |    submits retention job           |
|                                     v                                    |
|                           +-------------------+                          |
|                           |    BACKUP JOB     |                          |
|                           | (type=backup_full)|                          |
|                           +---------+---------+                          |
|                                     |                                    |
|                                     v                                    |
|                           +-------------------+                          |
|                           |   RETENTION JOB   |                          |
|                           | (type=retention)  |                          |
|                           +---------+---------+                          |
|                                     |                                    |
|            +------------------------+------------------------+           |
|            |                                                 |           |
|            v                                                 v           |
|   +------------------+                              +----------------+   |
|   | RETENTION HISTORY|                              |  STARROCKS S3  |   |
|   | - STARTED        |                              | DROP SNAPSHOT  |   |
|   | - DROPPED        |                              | (old copies)   |   |
|   | - FINISHED       |                              +----------------+   |
|   +------------------+                                                   |
+--------------------------------------------------------------------------+
```

---

## 3. Deconstructed Domain Requirements & Policies

### 3.1 Retention Job as Domain Peer & Concurrency Scope
- **Job Entity (`SPEC.md` §2, §23)**: `JobType.RETENTION` ("retention"). It has an id, status, timestamps, and parameters (`schedule_id`).
- **Concurrency Scope**: Reserves `concurrency.reserve_job_slot(..., scope="backup", label=f"retention_sched_{schedule_id}_job_{job_id}")`.
- **Policy**: Per `AGENTS.md` and Decision 0.6, retention shares the `backup` scope. A retention job cannot run concurrently with a backup job or another retention job on the same cluster.
- **Conflict Behavior**: If a backup is already running when retention executes, `reserve_job_slot` raises `ConcurrencyConflictError`. The retention job records `FAILED` in its history. The system is self-healing: the next successful full backup will re-evaluate all un-pruned backups.

### 3.2 Schedule Independence (`SPEC.md` §11)
- Retention evaluates full backups belonging strictly to `job.schedule_id == schedule.id`.
- Example:
  - Schedule A: Cadence 1h, Retention 5
  - Schedule B: Cadence 24h, Retention 7
  - Both target Inventory Group "production".
  - Schedule A retains its 5 newest successful full backups.
  - Schedule B retains its 7 newest successful full backups.
  - Neither schedule can prune the other's backups.

### 3.3 Selection Algorithm & The Baseline Exception (`SPEC.md` §21, §22, Decision Q1)
The selection query evaluates:
1. Query all `Job` records where `schedule_id == schedule.id`, `job_type == "backup_full"`, `status == "SUCCESS"`, and at least one reference has `deleted_at IS NULL`.
2. Sort newest to oldest (`finished_at.desc(), id.desc()`).
3. Keep the newest `schedule.retention` copies (e.g. 5).
4. Remaining full backups are candidates for deletion.
5. **Incremental Baseline Protection**: Query all distinct `baseline_job_id` values from `Job` where `cluster_id == cluster.id`, `job_type == "backup_incremental"`, and `status IN ('PENDING', 'RUNNING', 'SUCCESS')`.
6. Any candidate job whose `id` is in that baseline set is **skipped and preserved**.
7. All other candidate jobs are dropped.

### 3.4 Soft Data Deletion vs Metadata Preservation (`SPEC.md` §13, §14)
- **StarRocks/S3**: Calls `DROP SNAPSHOT ON {repository} WHERE SNAPSHOT = {snapshot_label}` for each distinct snapshot of the pruned job.
- **Metadata**: Updates `BackupReference.deleted_at = now`.
- **Preservation**: The `Job` row and its `backup_history` rows are **never deleted**. The backup job remains a historical record.
- **Lineage Filtering**: Queries resolving restore targets or incremental baselines (`find_latest_full_backup_job`, `find_latest_full_backup_before`, `list_partitions_for_label`) filter out references where `deleted_at IS NOT NULL`.

---

## 4. Confirmed Technical Decisions

### Decision 1: Trigger Retention via Post-Job Success Hook
- **Trigger**: Handled in a centralized hook `commands.jobs.finish_job_success(job_id, result_json)` called by execution workers (e.g. `thread_backend.py`) immediately after committing `Job.status = 'SUCCESS'`.
- **Rationale**: Guarantees the triggering backup job is already persisted as `SUCCESS` before the retention worker queries candidates. If retention submission fails, the backup job's status remains durable and unaffected.

### Decision 2: Dedicated `RetentionHistory` Table
- **Model**: `retention_history` table (`job_id`, `ts`, `status`, `message`, `details_json`).
- **Events**: `RETENTION_STARTED`, `SNAPSHOT_DROPPED`, `RETENTION_FINISHED`, `ERROR`, `FAILED`.
- **Query**: Mapped in `_HISTORY_MODEL_BY_JOB_TYPE["retention"] = RetentionHistory`, exposed via `GET /job/{id}/history`.

### Decision 3: Immediate Per-Job Commits of `deleted_at`
- **Execution Flow**: When multiple jobs are candidates for pruning, `deleted_at = now` is committed per job immediately after that job's snapshots are dropped in StarRocks.
- **Rationale**: If dropping Job 2 fails after Job 1 succeeds, Job 1 is already marked deleted in metadata. The next retention run will not attempt to re-drop Job 1 and will only retry Job 2.

### Decision 4: Idempotent Snapshot Drops
- **Rule**: If `DROP SNAPSHOT` indicates a snapshot is already absent in the repository, it is treated as successfully dropped.
- **Rationale**: Matches Section 8's `schedule_cleanup` idempotency rule.

### Decision 5: Cluster-Wide Active Incremental Baseline Definition
- **Scope**: Any incremental backup with `status IN ('PENDING', 'RUNNING', 'SUCCESS')` on any schedule across the cluster protects its baseline full backup.

### Decision 6: Retirement of Manual Prune Route
- **API Impact**: `POST /backup/manual/prune/cluster/{cluster_id}` is retired and returns 404 (**BREAKING**).
- **Cleanup**: `commands/prune.py` and `dal/metadata/prune.py` are removed.

---

## 5. Verification Strategy & Test Scenarios

1. **The `SPEC.md` §22 Test Case**:
   - 8 full backups executed over time: Jobs 1, 2, 4, 6, 7, 8 succeed; Jobs 3 and 5 fail.
   - Schedule retention = 5.
   - Verified that Jobs 8, 7, 6, 4, 2 are kept; only Job 1 is dropped; Jobs 3 and 5 are ignored.
   - Baseline variation: Job 9 (incremental) depends on Job 1. Verified that Job 1 is spared and 0 jobs are dropped.
2. **Schedule Independence (`SPEC.md` §11)**:
   - Schedule A (retention 2) and Schedule B (retention 3) share Inventory Group 1.
   - Verified that Schedule A dropping its oldest backup does not affect Schedule B's backups.
3. **Concurrency Serialization**:
   - Verified that an active backup job prevents a retention job from acquiring the slot, and vice versa.
4. **Failure Isolation**:
   - Verified that a simulated failure during `DROP SNAPSHOT` leaves the triggering backup job in status `SUCCESS` while the retention job is marked `FAILED`.
