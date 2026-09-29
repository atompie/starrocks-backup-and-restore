# StarRocks Backup System — Domain Specification

## 1. System Purpose

The system is used to manage backups of StarRocks clusters.

The system provides the ability to:

* define StarRocks clusters,
* define repositories where backups are stored,
* define inventories specifying what should be backed up,
* define schedules specifying:

  * what to back up,
  * where to store the backup,
  * when to perform the backup,
  * how many successful copies to retain,
* automatically execute backups,
* queue a backup immediately (a one-shot Schedule),
* track every backup execution through a Backup Job,
* maintain a detailed execution log for every Job,
* automatically enforce retention,
* restore data from any successfully completed backup job,
* track the restore process through Restore Job and Restore History.

---

# 2. Domain Model

Main entities:

```text
Cluster
Repository
Inventory
Schedule
Backup Job
Backup History
Restore Job
Restore History
Backup Reference
Retention Job
Retention History

```

Relationships:

```text
Cluster
 ├── Repository (1:N)
 ├── Inventory  (1:N)
 └── Schedule   (1:N)

Inventory
 └── Schedule   (1:N)

Repository
 └── Schedule   (1:N)

Schedule
 └── Backup Job (1:N)

Backup Job
 ├── Backup History   (1:N)
 └── Backup References (1:N)

Backup Job
 └── Restore Job (1:N)

Restore Job
 └── Restore History (1:N)

Schedule
 └── Retention Job (1:N)

Retention Job
 └── Retention History (1:N)

```

A Retention Job also references the Backup Jobs whose data it deletes (see §23), without owning
them the way a Schedule owns its Backup Jobs.

---

# 3. Cluster

`Cluster` represents a specific StarRocks installation / cluster that the system can connect to.

The Cluster owns:

* the StarRocks connection configuration,
* Inventory,
* Repository,
* Schedule.

For example:

```text
Cluster
├── Inventory A
├── Inventory B
├── Repository A
├── Repository B
└── Schedule A

```

## Relationships

Cluster → Inventory:

```text
1 Cluster
  └── N Inventory

```

Cluster → Repository:

```text
1 Cluster
  └── N Repository

```

Cluster → Schedule:

```text
1 Cluster
  └── N Schedule

```

---

# 4. Repository

`Repository` represents a backup storage location associated with a specific Cluster.

A Cluster may have multiple repositories.

```text
Cluster A
├── Repository 1
├── Repository 2
└── Repository 3

```

A Repository is not global.

It belongs to exactly one Cluster:

```text
Repository → Cluster = N:1

```

A Schedule points to a specific Repository.

This allows one Cluster to have different backup storage locations, while different Schedules can use different repositories.

## Reference

A Repository is not tracked as a separate entity in the system's own metadata; StarRocks itself
is the source of truth for what repositories exist. Wherever the system needs to point at one —
a Schedule, a Backup Reference — it does so by `(cluster, repository name)`, validated live against
that cluster's repositories. StarRocks enforces the name's uniqueness within a cluster, so no
separate uniqueness constraint is needed.

The `(cluster_id, name)` pair is the reference pattern for a Repository everywhere it is used:
the same name on two different clusters identifies two different repositories, and a reference
is validated only against the repositories of the one cluster it names. A Schedule's `repository`
is validated live against its own Cluster on both creation and update; a Backup Reference records
the repository its data was written to (§14-15).

---

# 5. Inventory

`Inventory` defines **what should be backed up**.

An Inventory belongs to exactly one Cluster.

```text
Cluster
 └── Inventory
       ├── Database A
       │    ├── Table 1
       │    ├── Table 2
       │    └── Table 3
       │
       └── Database B
            └── ALL TABLES

```

An Inventory may define:

* a specific database and specific tables,
* an entire database.

Example:

```text
Inventory: production

database: orders
tables:
  - customers
  - orders
  - order_items

```

or:

```text
Inventory: production

database: orders
tables: ALL

```

An Inventory does not define:

* when to perform the backup,
* where to perform the backup,
* how many copies to retain.

Those properties belong to the Schedule.

## Reuse

An Inventory can be used by multiple Schedules.

Example:

```text
Inventory: production
        │
        ├── Schedule A → every hour
        ├── Schedule B → once a day
        └── Schedule C → manual

```

The Inventory is therefore a reusable definition.

---

# 6. Schedule

`Schedule` is the central entity defining the backup execution policy.

A Schedule defines:

> **what + where + when + how many copies to retain**

A Schedule contains:

```text
Schedule
├── Cluster
├── Inventory
├── Repository
├── Cadence
├── Retention
└── Enabled

```

### A Schedule references:

* `Inventory` — what to back up,
* `Repository` — where to store the backup,
* `Cadence` — when to perform the backup,
* `Retention` — how many successful full backups to retain (recurring full-backup Schedules only, §10).

The Inventory and Repository must belong to the same Cluster as the Schedule.

The following is not allowed:

```text
Schedule
  Cluster A
  Inventory A
  Repository B (Cluster B)

```

A Schedule must be consistent within a single Cluster.

---

# 7. Cadence

`Cadence` defines how frequently a backup is executed.

For example:

```text
every 1 hour
every 6 hours
every day
cron expression

```

The exact cadence format is an implementation detail.

## Cadence = null

`cadence = null` has a special meaning:

> **queue a backup now.**

This represents a one-shot / manual backup Schedule. It must be a full backup, and it has no
retention: retention applies only to recurring full-backup Schedules (§10).

Example:

```text
Schedule
inventory  = production
repository = repo-1
cadence    = null

```

means:

> Create a full Backup Job now; it starts like any other job (§19).

---

# 8. Manual Schedule

A Schedule with `cadence = null` is persisted in the system.

It is not a temporary object.

After creation:

* it cannot be edited,
* it can be deleted, either by an operator or automatically once its expiry elapses,
* it may define an expiry: never, or after a configured number of days,
* it is used to execute one backup.

The execution creates a normal `Backup Job`.

If an expiry is configured, the Schedule is removed automatically once the expiry period has
elapsed, through the same cascading process as an operator-requested delete (see §24).

The system should not have a separate execution mechanism for manual backups. A manual backup should go through the same Job mechanism as a scheduled backup.

The only difference is:

```text
cadence = null

```

which means the Backup Job is created immediately.

---

# 9. Recurring Schedule

A Schedule with a configured `cadence` is an active definition.

Example:

```text
Schedule A

Inventory: production
Repository: repo-prod
Cadence: every 1 hour
Retention: 5

```

One Schedule can generate multiple Backup Jobs:

```text
Schedule A
   │
   ├── Job 101
   ├── Job 102
   ├── Job 103
   ├── Job 104
   └── Job 105

```

The Schedule is the definition, while the Job is a specific execution.

---

# 10. Retention

The system supports two kinds of backup: a full backup, which captures an entire database (or
the tables an Inventory specifies), and an incremental backup, which captures only the data
changed since a specific full backup, its baseline. An incremental backup cannot be restored on
its own; it is always applied on top of its baseline full backup.

`Retention` defines the **number of copies**, not the retention period, and it applies only to
the full backups of a recurring full-backup Schedule. It is required for such a Schedule and not
allowed on any other (recurring incremental, or one-shot).

Example:

```text
retention = 5

```

means:

> Keep the 5 most recent successfully completed full backups of this Schedule.

Incremental backups are not subject to retention: they are never counted towards `retention` and
are never deleted by it. Some older full backups are also protected from it — see §21.

Retention is a property of the Schedule.

---

# 11. Retention Is Independent for Each Schedule

Each Schedule has its own pool of backups subject to retention.

Example:

```text
Inventory: production

Schedule A
cadence: 1h
retention: 5

Schedule B
cadence: 24h
retention: 7

```

Schedule A retains its 5 most recent successful full backups.

Schedule B retains its 7 most recent successful full backups.

Backups are not counted together simply because they:

* use the same Inventory,
* use the same Repository,
* belong to the same Cluster.

Retention always operates **within a specific Schedule**.

---

# 12. Backup Job

`Backup Job` represents **one specific execution of a backup**.

If a Schedule runs every hour:

```text
Schedule A
   │
   ├── Job 101 → 10:00
   ├── Job 102 → 11:00
   ├── Job 103 → 12:00
   └── Job 104 → 13:00

```

A Job is a historical fact:

> A backup was started at a specific time as part of a specific Schedule.

The Job should store the information required to restore the backup later.

---

# 13. Job Exists Independently of the Result

A Backup Job is created whenever the system queues a backup execution.

It can finish with:

```text
SUCCESS
FAILED

```

A failed Job is not deleted.

Example:

```text
Job 101 → SUCCESS
Job 102 → SUCCESS
Job 103 → FAILED
Job 104 → SUCCESS

```

Job 103 remains in the history.

It indicates that the backup was started but the execution did not complete successfully.

---

# 14. Backup Job as the Source of a Recoverable Backup

The system does not have a separate entity representing the physical backup.

The physical backup exists as files/data in the Repository.

The `Backup Job` stores references to that data.

Model:

```text
Backup Job
   │
   └── Backup References
          │
          ▼
      Repository
          │
          ▼
      backup files

```

After a successful Job, the Job contains references that allow the backup to be located in the Repository.

Therefore, the `Backup Job` is the source of information answering:

> Which execution produced this data, and where is the data required for restore located?

---

# 15. Backup Reference

`Backup Reference` points to the physical backup data stored in the Repository.

It may contain information such as:

```text
repository
path
object identifier
backup identifier
database
table

```

The exact format depends on how StarRocks performs backups and how the Repository is implemented.

The key principle is:

> A Backup Reference is not the backup itself. It is a reference to data stored in the Repository.

---

# 16. Failed Job

A failed Job is not a recoverable backup.

Example:

```text
Job 103
status = FAILED
backup references = ...

```

If partial data remains in the Repository after a failed execution, it must not be treated as a valid backup that can be restored.

Only a backup resulting from a successfully completed Job is recoverable.

---

# 17. Backup History

Each Backup Job has its own execution log.

Relationship:

```text
Backup Job
    │
    └── 1:N Backup History

```

History is an **execution log for the Job**, not just a single status field.

A single Job can have multiple entries. Each entry's status is the state StarRocks itself reports
while the backup runs (via `SHOW BACKUP`), plus a final `SUCCESS` or `FAILED` entry this system
adds once the Job completes; a new entry is recorded only when the state changes, so the log stays
short. The exact set of intermediate states StarRocks reports is an implementation detail (see
`PLAN.md` §4).

```text
Job 123

10:00:01 PENDING
10:00:03 SNAPSHOTING
10:00:20 UPLOADING
10:01:00 SAVE_META
10:01:01 SUCCESS

```

In case of an error:

```text
Job 124

10:00:01 PENDING
10:00:03 SNAPSHOTING
10:00:18 FAILED

```

History makes it possible to determine:

* what happened,
* when it happened,
* at which stage the problem occurred,
* what the error was,
* whether the operation completed successfully.

History should be treated as an append-only log.

---

# 18. Job Status

The Job status represents the current/final execution state.

At minimum:

```text
PENDING
RUNNING
SUCCESS
FAILED

```

History may contain much more detailed events than the status itself.

Status does not replace History.

---

# 19. Jobs: Lifecycle, Scheduling, and Relationships

Every operation the system runs against a Cluster is a Job with an id, a status, and an
append-only execution log (its History):

| Job | Created when | Operates on | Depends on |
|---|---|---|---|
| Backup Job (full or incremental) | a recurring Schedule is due (scheduler tick); a one-shot Schedule is created | the Schedule's Inventory, into its Repository | incremental: its baseline full Backup Job |
| Retention Job | a recurring full-backup Schedule has droppable backups (scheduler tick) | that Schedule's successful full Backup Jobs and their snapshots | those Backup Jobs, which it never modifies |
| Restore Job | an operator requests it | the data of one successful Backup Job (and its baseline), into the target Cluster | its source Backup Job |
| Schedule cleanup | a Schedule is deleted or its expiry elapses (§24) | the Schedule's Backup Jobs and snapshots | — |

### Lifecycle

```text
created ──> PENDING ──(admitted by a scheduler tick)──> RUNNING ──> SUCCESS
                                                           └─────> FAILED
```

* Creating a Job only queues it as `PENDING`. Only a scheduler tick starts jobs; if no tick runs,
  nothing runs.
* Each tick, in order: reconciles Jobs whose worker died (a `RUNNING` Job with no live heartbeat is
  failed, never promoted to `SUCCESS`; a `PENDING` Job is never stale), creates the Backup Jobs of
  due Schedules, creates the cleanup Jobs of expired one-shot Schedules (§8), creates the Retention Jobs
  that are needed (§21), then admits queued Jobs.
* **One Job runs at a time per Cluster, of any kind.** The next Job is chosen restore first, then
  backup, then everything else (retention, schedule cleanup), oldest first within a kind. Jobs on
  different Clusters run in parallel. A queued Job waits its turn; it does not fail because its
  Cluster is busy.
* A Job ends `SUCCESS` or `FAILED`. A `FAILED` Job always has a `FAILED` History entry carrying the
  error. One Job's failure never changes the status of another Job.
* A Job is never deleted, except by §24 (and a Restore Job together with its source, §27).

### Backup Job

* Created `PENDING` with the Schedule's Inventory, Repository, and type (full or incremental). A
  due recurring Schedule that still has a `PENDING` or `RUNNING` Backup Job gets no additional one;
  it moves to its next occurrence.
* Runs one StarRocks backup per database of the Inventory. Its Backup References are recorded once
  the StarRocks operation for a database reaches `FINISHED`.
* An incremental backup's baseline, per database, is the most recent successful full backup on the
  Cluster covering that database (from any Schedule) whose data has not been deleted.
* `SUCCESS` makes it a recoverable backup; `FAILED` does not (§16).

### Retention Job

* Created by the tick for a recurring full-backup Schedule when it has droppable backups (§21) and
  no `PENDING` or `RUNNING` Retention Job. It is not tied to any particular Backup Job finishing.
* Operates only on that Schedule's own backups (§11). It drops each droppable backup's snapshots
  and marks that backup's References deleted, one backup at a time.
* It stops starting new drops once its configured time limit has passed, and ends normally; the
  next tick creates another Retention Job for what remains.
* If it fails, the backups not yet dropped are simply retried by a later Retention Job. It never
  changes the status of any Backup Job.

### Restore Job

* Created `PENDING` by an operator's request, naming a source Backup Job and a target Cluster
  (§26-27). A request whose source Schedule is pending deletion is rejected.
* A source backup that a `PENDING` or `RUNNING` Restore Job uses is protected from retention (§21).
* Restore does not modify the source Backup Job (§29).

---

# 20. Checking Whether a Backup Is Due

For a recurring Schedule, the tick determines whether the next execution is due according to the
`cadence`, and creates the Backup Job (§19).

Example:

```text
Schedule
cadence = every 1 hour

last execution = 10:00
current time    = 11:00

```

→ the backup is due.

A `cadence = null` Schedule creates its one Backup Job when it is created (§7-8).

---

# 21. Retention: What Is Kept and What Is Dropped

Retention works on one recurring full-backup Schedule's pool (§11), evaluated by a Retention Job
(§23):

1. The pool is the Schedule's successful full Backup Jobs whose data has not yet been deleted.
   Failed Jobs are ignored (§22).
2. Sort the pool from newest to oldest. The newest `retention` are kept.
3. The rest are droppable, except those that are protected:
   * a full backup that is the baseline of any incremental backup on the Cluster that is
     `PENDING`, `RUNNING`, or `SUCCESS` (for this Schedule or any other) — it becomes droppable
     once no incremental depends on it;
   * a backup used as the source (or as the baseline of the source) of a Restore Job that is
     `PENDING` or `RUNNING`.
4. A Retention Job is needed for a Schedule exactly when at least one droppable backup exists.
   Protected backups alone never require one.

Dropping a backup deletes its snapshots from the Repository (a snapshot that is already gone counts
as dropped) and marks its Backup References deleted. The Backup Job and its History remain (§13-14).
A backup whose data is deleted can no longer be a baseline or a Restore source. This same
protection applies when a Schedule or Cluster is deleted (§24).

---

# 22. Example: Failed Jobs and Retention

Failed Jobs are ignored when calculating retention.

```text
Job 1 → SUCCESS
Job 2 → SUCCESS
Job 3 → FAILED
Job 4 → SUCCESS
Job 5 → FAILED
Job 6 → SUCCESS
Job 7 → SUCCESS
Job 8 → SUCCESS

```

With `retention = 5` the relevant Jobs, newest first, are 8, 7, 6, 4, 2, 1; Jobs 3 and 5 are not
considered. The newest five (8, 7, 6, 4, 2) are kept and Job 1, the sixth, is dropped.

Baseline variation: if Job 1 is the baseline of an incremental backup, Job 9, Job 1 is protected
and nothing is dropped; Job 1 becomes droppable again once Job 9 (and any other incremental
depending on it) no longer exists.

---

# 23. Retention Job

`Retention Job` is one execution of retention for a Schedule (rules in §21, lifecycle in §19). It
has an id, a status, and its own execution log, `Retention History`, whose entries record that it
started, each snapshot it dropped, and how it ended (finished, or the error).

```text
Schedule
   └── Retention Job
          ├── Retention History
          └── drops old Backup Jobs' snapshots (and marks their References deleted)
```

A Retention Job references the Backup Jobs whose data it deletes without owning them. It does not
change their status, and deleting their data does not delete the Backup Jobs, which remain
historical records (§13-14).

---

# 24. Deleting a Schedule or Cluster

Deleting a Schedule, whether requested by an operator or triggered automatically by a one-shot
Schedule's expiry (see §8), removes every Backup Job the Schedule produced, their Backup History
and Backup References, and the corresponding snapshots in the Repository. This is a cascading
operation, not the removal of a single row.

Because it involves removing data from the Repository, deleting a Schedule is not instantaneous:

1. the request is checked immediately — it is rejected if the Schedule has a Backup Job still in
   progress, or a Restore Job using one of its backups still in progress, or if one of its full
   backups is currently the baseline of an incremental backup belonging to a different Schedule
   (see §21);
2. once accepted, the removal itself — dropping snapshots from the Repository and deleting the
   Schedule's Jobs, History, and References — runs as its own tracked Job, queued and run like any
   other (§19).

A Restore Job that used one of the deleted Backup Jobs as its source is deleted along with it
(see §27); it exists only as a record of that specific restore, and has no meaning once its
source backup is gone.

Deleting a Cluster follows the same two-step shape. A Cluster can only be deleted once it has no
Schedules at all — enabled or disabled. Once that condition holds, deleting it cascades the same
way: every remaining Backup Job, Retention Job, and Restore Job (whether the Cluster was its
source or its target) belonging to that Cluster is removed. Any Repository registered on that
Cluster is left as-is in StarRocks — Repository is not owned by this system's metadata (see §4),
so deleting a Cluster does not attempt to unregister or drop it.

---

# 25. Restore Job and Its Source

A Restore Job always refers to one specific successful Backup Job (§26). One Backup Job can be the
source of multiple Restore Jobs:

```text
Backup Job 123
    │
    ├── Restore Job 1
    ├── Restore Job 2
    └── Restore Job 3

```

---

# 26. Restore Can Use Any Successful Backup Job

The user can select any successfully completed Backup Job whose data is still available: its
References have not been deleted by retention (§21), and its Schedule is not pending deletion. It
does not have to be the latest or newest backup. An incremental backup is restored together with
its baseline.

---

# 27. What a Restore Job Records

A Restore Job records its `source_backup_job` and `target_cluster`, its status, and its own
Restore History (§28); its lifecycle is in §19.

Unlike a Backup Job, a Restore Job's only purpose is to record that specific restore attempt. It
is deleted if its source Backup Job is deleted (§24), or if either the source or target Cluster is
deleted: once its source backup or either Cluster is gone, keeping the record serves no purpose.

---

# 28. Restore History

A Restore Job has its own execution log:

```text
Restore Job
    │
    └── 1:N Restore History

```

As with Backup History, each entry's status is the state StarRocks itself reports while the
restore runs (via `SHOW RESTORE`), plus a final `SUCCESS` or `FAILED` entry this system adds once
the Job completes; a new entry is recorded only when the state changes.

Example:

```text
10:00:01 PENDING
10:00:05 DOWNLOADING
10:00:30 COMMITTING
10:01:01 SUCCESS

```

In case of an error:

```text
10:00:01 PENDING
10:00:10 DOWNLOADING
10:00:10 FAILED

```

---

# 29. Restore Does Not Modify the Backup Job

A backup is a historical artifact.

Executing a Restore does not modify:

```text
Backup Job
Backup History
Backup References

```

Restore is a separate operation that uses an existing backup.

It is therefore possible to have:

```text
Backup Job #100
    ├── Restore #1 → FAILED
    ├── Restore #2 → SUCCESS
    └── Restore #3 → SUCCESS

```

---

# 30. Full Lifecycle

```text
Cluster
   ├── Inventory
   ├── Repository
   └── Schedule
          │ cadence (scheduler tick)
          ▼
      Backup Job ── PENDING ──(tick admits, one job per Cluster)──> RUNNING ──> SUCCESS / FAILED
          │
          ├── Backup History
          └── Backup References ──> Repository
                    │
                    │ more successful full backups than `retention` (tick)
                    ▼
              Retention Job ──> drops old snapshots, marks References deleted
                    │
                    └── Retention History

Successful Backup Job (data not deleted)
        │ restore request
        ▼
   Restore Job ── Restore History
```

---

# 31. Key Domain Rules

### Cluster

* A Cluster represents a specific StarRocks installation.
* A Cluster can have multiple Inventories.
* A Cluster can have multiple Repositories.
* A Cluster can have multiple Schedules.

### Inventory

* An Inventory belongs to exactly one Cluster.
* An Inventory defines what should be backed up.
* An Inventory can specify specific tables or an entire database.
* An Inventory can be used by multiple Schedules.

### Repository

* A Repository belongs to exactly one Cluster.
* A Cluster can have multiple Repositories.
* A Schedule points to a specific Repository.

### Schedule

* A Schedule belongs to one Cluster.
* A Schedule points to exactly one Inventory.
* A Schedule points to exactly one Repository.
* The Inventory and Repository must belong to the same Cluster.
* A Schedule defines the cadence.
* A recurring full-backup Schedule defines the retention; no other Schedule has one.
* Retention is a number of copies.
* Retention is independent for each Schedule.
* `cadence = null` means the Backup Job is created immediately; it must be a full backup.
* A one-shot Schedule is immutable after creation.
* A one-shot Schedule can be deleted.
* A recurring Schedule generates multiple Backup Jobs.

### Jobs

* Creating a Job only queues it (`PENDING`); only a scheduler tick starts Jobs.
* One Job runs at a time per Cluster; the next is restore, then backup, then other Jobs, oldest first.
* Jobs on different Clusters run in parallel; a queued Job never fails because its Cluster is busy.
* A Job ends `SUCCESS` or `FAILED`; a Job's failure never changes another Job's status.

### Backup Job

* One Job represents one backup execution.
* A Job exists independently of its result.
* A Job can complete successfully or fail.
* A Job has its own History.
* A Job stores references to the backup data.
* Only a successfully completed Job can be used as a Restore source.
* A failed Job is not considered during retention.

### Backup

* There is no separate domain entity representing a Backup.
* The physical backup is stored in the Repository.
* The Backup Job stores references to the physical data.
* The references allow the backup to be restored later.

### Retention

* Retention is defined by a recurring full-backup Schedule and applies only to its full backups.
* Only successfully completed full Backup Jobs whose data is not yet deleted are counted; Failed Jobs are ignored.
* The newest N are kept; older ones are dropped by a Retention Job, except backups that are the
  baseline of an active or successful incremental, or the source of a `PENDING`/`RUNNING` Restore Job.
* Dropping deletes the snapshots and marks the References deleted; the Backup Job and its History remain.
* A Retention Job never changes the status of any Backup Job.

### Restore

* Restore is a separate Job.
* A Restore references a specific successful Backup Job whose data is not deleted.
* One Backup Job can be the source of multiple Restore Jobs.
* Restore has its own History.
* Restore does not modify the history of the source Backup Job.

---

# 32. Conceptual Definition of the Entities

The simplest way to understand the system is:

```text
CLUSTER
    Where is the StarRocks cluster?

INVENTORY
    What do we want to back up?

REPOSITORY
    Where do we physically store the backup?

SCHEDULE
    What + where + when + how many copies?

BACKUP JOB
    A specific execution of a Schedule.

BACKUP HISTORY
    What exactly happened during the Job execution.

BACKUP REFERENCE
    Where the data created by the Job is located.

RETENTION JOB
    A specific execution of retention for a Schedule.

RETENTION HISTORY
    What exactly happened during the Retention Job.

RESTORE JOB
    A specific attempt to restore data from a Backup Job.

RESTORE HISTORY
    What exactly happened during the Restore.

```

The most important separation of responsibilities is:

```text
              DEFINITION
                  │
             ┌────┴────┐
             │ Schedule│
             └────┬────┘
                  │
               EXECUTION
                  │
             ┌────▼────┐
             │BackupJob│
             └────┬────┘
                  │
          ┌───────┴────────┐
          │                │
       HISTORY          REFERENCES
          │                │
          │                ▼
          │           REPOSITORY
          │
          └───────┐
                  │
              RESTORE
                  │
             ┌────▼────┐
             │RestoreJob│
             └────┬────┘
                  │
             RestoreHistory

```

In this model, **the Schedule describes the intent**, **the Job describes the execution**, **the Repository stores the actual data**, and **the Restore Job describes the operation of recovering that data**.
