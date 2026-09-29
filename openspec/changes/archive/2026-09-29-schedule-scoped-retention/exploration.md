# Exploration: Section 10 — Schedule-Scoped Retention (Own Job)

A deep exploration of `SPEC.md` (§2, §10, §11, §21-23), `PLAN.md` (§0.5, §0.6, §Q1, §Q4, §10), and the codebase for implementing schedule-scoped retention as an independent, automated job. This revision records a second exploration round that changed how retention is triggered and serialized, and the options that were rejected on the way.

---

## 1. Executive Summary & Problem Space

Retention is an **automated, schedule-scoped lifecycle operation** modeled as a first-class peer of Backup and Restore jobs:

1. **Independent Retention Pools (`SPEC.md` §11)**: retention operates strictly per schedule (`Schedule.retention`). Two schedules on the same inventory group and repository keep separate pools.
2. **Metadata Preservation (`SPEC.md` §13, §14)**: dropping old backups deletes data from StarRocks/S3 and marks `BackupReference.deleted_at = now`, but **never deletes `Job` rows or `backup_history`**.
3. **Incremental Baseline Protection (`SPEC.md` §21, §22, Decision Q1)**: a full backup that is the baseline of an existing incremental is protected indefinitely.
4. **Failure Isolation (`SPEC.md` §23, `PLAN.md` §10.4)**: retention failures mark only the retention job `FAILED`; they never change a backup job.
5. **Retirement of Legacy Prune**: `POST /backup/manual/prune/cluster/{cluster_id}` groups backups across schedules by inventory group and hard-deletes `Job` rows, violating §11 and §13-14. It is retired.
6. **Lowest priority (added in round 2)**: retention is cleanup. It must never make a backup or restore fail or be delayed by anything but a bounded wait, and it may itself wait.

---

## 2. What the second round found (why the first design changed)

The first design triggered retention from a post-success hook (`finish_job_success`) in the thread backend and
serialized it by reserving the `backup` `RunStatus` slot. Reading the code showed:

```text
 tick (sequential, holds scheduler lock)            jobs (parallel, ThreadBackend pool of 8)
   submit_job() = create row + enqueue  ------->     backup A .......
   returns immediately                               backup B .......   <- same tick, same cluster
                                                     retention(A) ....  <- submitted by A's worker
```

- **The tick is sequential but does not run jobs.** Two schedules due in one tick already run in parallel; the
  `RunStatus` slot is the only guard and the loser fails with `ConcurrencyConflictError`.
- **A retention slot would collide with backups.** `reserve_job_slot` raises on conflict, so a backup that fires
  while retention holds `backup` would itself become `FAILED` - unacceptable for the lowest-priority job.
- **Stale-slot healing misfires for retention.** `is_backup_job_stale` asks `SHOW BACKUP` about a label and returns
  "stale" when no database lists it. A retention slot label is never a StarRocks snapshot, so a live retention slot
  looks stale and is silently cancelled by the next backup, and the two run concurrently with no error.
- **Weak retry.** A retention that failed on a busy slot only retried on the schedule's next full backup (a week for
  a weekly schedule).
- **Restore gap.** The design protected only incremental baselines; a snapshot that a `PENDING`/`RUNNING` restore
  reads from could be dropped.

### Options that were considered and rejected

```text
+----+--------------------------------------------------+---------------------------------------------+
|    | Option                                           | Why not                                     |
+----+--------------------------------------------------+---------------------------------------------+
| A  | Preemptible retention slot (backup cancels it,   | Labels, cancel handshake, heartbeat heal,   |
|    | retention checks its slot between drops)         | _fail_job changes: complicated.             |
| B  | Backup waits (polls) for retention; slot has     | Parks a pool thread per waiter; TTL and     |
|    | expires_at TTL                                   | expiry logic; still first-come ordering.    |
| C  | Separate retention tick / process                | Still needs the slot (check-then-act); not  |
|    |                                                  | sequenced with the backup tick.             |
| D  | Run retention inline at the end of the backup    | Conflicts with SPEC 23 (own Job/history) and|
|    | job                                              | failure isolation.                          |
| E  | Central dispatcher in the tick: one lane per     | CHOSEN - see cluster-job-dispatcher.        |
|    | cluster, queued jobs are PENDING rows            |                                             |
+----+--------------------------------------------------+---------------------------------------------+
```

The user's framing that produced E: different clusters may run concurrently, jobs on the same cluster must go one
after another, and the tick (the main loop) should be what says "now backup, now retention". That is admission
control owned by the tick, which is exactly what the separate change `cluster-job-dispatcher` builds.

---

## 3. Architecture & Interaction Topology (final)

```text
 tick (scheduler lock)
   1 reconcile stale RUNNING jobs
   2 run_due_schedules        -> PENDING backup jobs (skips a schedule with an open job)
   3 expire_due_schedules
   4 submit_due_retention_jobs   (this change)
        for each recurring full-backup schedule:
           droppable candidates exist AND no open retention job -> submit retention job (PENDING)
   5 dispatch_pending_jobs    (cluster-job-dispatcher)
        per cluster: nothing RUNNING -> admit highest priority (restore > backup > other), oldest first

 cluster lane:  [ backup ...... ] -> next tick -> [ retention ... ] -> next tick -> [ backup ... ]
                retention never overlaps anything; it takes no RunStatus slot
```

Retention job internals:

```text
 run_retention
   RETENTION_STARTED
   for each droppable backup (newest-kept rule, protections applied):
       deadline passed?            -> stop, RETENTION_FINISHED (deadline_reached), status SUCCESS
       protections still hold?     -> re-check (baseline, active restore source)
       DROP SNAPSHOT per (repository, label); already absent = dropped
       SNAPSHOT_DROPPED
       short session: BackupReference.deleted_at = now (this backup)
   RETENTION_FINISHED
```

---

## 4. Deconstructed Domain Requirements & Policies

### 4.1 Retention Job as a Domain Peer
- **Job Entity (`SPEC.md` §2, §23)**: `JobType.RETENTION` (`"retention"`) with id, status, timestamps, parameters (`schedule_id`) and its own history.
- **Serialization**: provided by the dispatcher's one-job-per-cluster lane. Retention does not call `reserve_job_slot`.
- **Priority**: lowest tier with schedule cleanup; a queued restore or backup always goes first.

### 4.2 Schedule Independence (`SPEC.md` §11)
Retention evaluates only `job.schedule_id == schedule.id`. Example: Schedule A (1h, retention 5) and B (24h, retention 7) on the same group each keep their own newest 5 / 7; neither can prune the other.

### 4.3 Selection Algorithm, Baseline and Restore Protection
1. Successful full backups of this schedule with at least one reference `deleted_at IS NULL`.
2. Newest first (`finished_at.desc(), id.desc()`); keep the newest `schedule.retention`.
3. Older ones are candidates; protect candidates that are the `baseline_job_id` of any incremental on the cluster with status `PENDING`/`RUNNING`/`SUCCESS`.
4. Protect candidates that are the source (or the baseline of the source) of a restore in `PENDING`/`RUNNING`. Checked at selection and again right before each drop.
5. The rest are droppable. The sweep uses the same query, so protected old backups do not create a retention job every tick.

### 4.4 Soft Data Deletion vs Metadata Preservation
- StarRocks/S3: `DROP SNAPSHOT ON {repository} WHERE SNAPSHOT = {snapshot_label}` per distinct snapshot.
- Metadata: `BackupReference.deleted_at = now`; `Job` and `backup_history` are never deleted.
- Lineage lookups (`find_latest_full_backup_job`, `find_latest_full_backup_before`, `list_partitions_for_label`) ignore references with `deleted_at` set.

---

## 5. Confirmed Technical Decisions

1. **Trigger is a tick sweep** (not a post-backup hook). Replaces "Decision 1: post-job success hook" of round 1. It is backend-agnostic, restart-safe and self-healing, and counts only droppable candidates.
2. **No slot for retention; serialization is the dispatcher's.** Supersedes the preemptible-slot, heartbeat-heal and expiring-slot ideas. Depends on `cluster-job-dispatcher` (archive it first).
3. **Deadline** `STARROCKS_BR_RETENTION_MAX_SECONDS` (default 1800): stop starting drops, end normally, resume next sweep. This is the user's "expiry" idea, applied inside the handler.
4. **Dedicated `RetentionHistory` table** (`RETENTION_STARTED`, `SNAPSHOT_DROPPED`, `RETENTION_FINISHED`, `ERROR`, `FAILED`), exposed by `GET /job/{id}/history`.
5. **Per-backup commits of `deleted_at`** and **idempotent drops** (already-absent snapshot counts as dropped).
6. **Cluster-wide incremental baseline protection** and **new: active-restore-source protection**.
7. **Retirement of the manual prune route** (BREAKING, returns 404); `JobType.PRUNE` enum entry kept for historical reads.
8. **Failure handling**: a failed retention job never touches backups; the next sweep retries undeleted backups. No "wait for the next backup" delay any more.

Explicitly accepted trade-off: retention may starve on a cluster that is never idle.

---

## 6. Verification Strategy & Test Scenarios

1. **`SPEC.md` §22**: 8 full backups (Jobs 3 and 5 fail), retention 5: keep 8, 7, 6, 4, 2; drop only Job 1. Baseline variation: Job 9 (incremental) depends on Job 1: Job 1 is spared and nothing is dropped.
2. **Schedule independence (§11)**: A (retention 2) and B (retention 3) on one group drop independently.
3. **Sweep**: no job when only protected backups remain; no duplicate while one is open; submitted for an over-quota schedule.
4. **Ordering with the dispatcher**: retention waits `PENDING` behind a `RUNNING` backup/restore; a backup submitted during retention waits and does not fail; a queued restore protects its source backup.
5. **Deadline**: stops starting drops, ends `SUCCESS` with the deadline noted, next tick resumes.
6. **Failure isolation**: a `DROP SNAPSHOT` error leaves every backup `SUCCESS`, the retention job `FAILED`, and the undeleted backup is retried later.

---

## 7. Open Items

- `finished_at` must be set on every `SUCCESS` full backup for the newest-first ordering (verify while implementing; falls back to `id`).
- Retention deadline default (1800 s) is a starting point.
- Dropping `RunStatus` slots and the `SHOW BACKUP` staleness heuristic (a heartbeat-only model) remains a separate, later change.
