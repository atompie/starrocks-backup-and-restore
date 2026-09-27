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
* manually trigger a backup immediately,
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
* `Retention` — how many successful backups to retain.

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

> **execute the backup now.**

This represents a one-shot / manual backup Schedule.

Example:

```text
Schedule
inventory  = production
repository = repo-1
cadence    = null
retention  = 5

```

means:

> Create a backup job and execute it now.

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

which means immediate execution.

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
full backups.

Example:

```text
retention = 5

```

means:

> Keep the 5 most recent successfully completed full backups of this Schedule.

Incremental backups are not subject to retention: they are never counted towards `retention` and
are never deleted by it. A full backup that currently serves as the baseline for an existing
incremental backup is also not subject to retention, regardless of how old it is or how many
newer full backups exist — see §21.

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

Schedule A retains its 5 most recent successful backups.

Schedule B retains its 7 most recent successful backups.

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

A Backup Job is created whenever the system starts a backup execution.

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

A single Job can have multiple entries:

```text
Job 123

10:00:01 STARTED
10:00:02 CONNECTING
10:00:03 BACKUP_STARTED
10:00:15 DATABASE_STARTED
10:00:20 TABLE_COMPLETED
10:01:00 BACKUP_FINISHED
10:01:01 SUCCESS

```

In case of an error:

```text
Job 124

10:00:01 STARTED
10:00:02 CONNECTING
10:00:03 BACKUP_STARTED
10:00:18 ERROR
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

# 19. Internal System Process

The system has an internal process responsible for handling Schedules.

Its responsibilities are to check:

1. whether a Schedule is due to run,
2. whether a Backup Job should be started,
3. whether the execution completed successfully,
4. whether retention should be applied after completion.

Logically:

```text
for each active Schedule:

    if backup_is_due(schedule):

        job = create_backup_job(schedule)

        execute_backup(job)

        if job.success:
            apply_retention(schedule)

```

---

# 20. Checking Whether a Backup Is Due

For a recurring Schedule, the system determines whether it is time for the next execution according to the `cadence`.

Example:

```text
Schedule
cadence = every 1 hour

last execution = 10:00
current time    = 11:00

```

→ the backup is due.

For:

```text
cadence = null

```

the Schedule is an immediate request and should be executed as a one-shot operation.

---

# 21. Retention After Backup Execution

After a full backup completes successfully, the system checks the Schedule's retention policy.
This process runs as its own Retention Job (see §23); it does not change the status of the
Backup Job that triggered it, or of any Backup Job whose data it deletes (see §13-14).

Example:

```text
Schedule
retention = 5

```

The system finds all:

```text
SUCCESSFUL full Backup Jobs

```

belonging to that Schedule.

It then sorts them from newest to oldest.

The newest 5 are retained.

Before deleting an older full backup, the system checks whether it is still the baseline of any
existing incremental backup, for this Schedule or any other. If it is, that backup is skipped
and kept, even though it falls outside the newest 5; it becomes eligible for deletion once no
incremental backup depends on it any longer. This same check applies when a Schedule or Cluster
is deleted (see §24).

Older backups (that are not a still-needed baseline) are deleted from the Repository.

---

# 22. Failed Jobs and Retention

Failed Jobs are completely ignored when calculating retention.

Example:

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

With:

```text
retention = 5

```

the relevant jobs are:

```text
Job 8
Job 7
Job 6
Job 4
Job 2
Job 1

```

Job 3 and Job 5 are not considered.

The backup to delete is:

```text
Job 1

```

because it is the sixth most recent successful backup.

## Baseline exception

Suppose Job 1 is still the baseline of an incremental backup, Job 9, that has not yet been
superseded by a newer full backup. Even though Job 1 is the sixth most recent successful full
backup and would normally be deleted, retention skips it and keeps it, because deleting it would
make Job 9 unrestorable. Job 1 becomes eligible for deletion again only once Job 9 (or any
incremental depending on it) no longer exists.

---

# 23. Retention Job

`Retention Job` represents one execution of the retention process for a Schedule.

It is a job in the same sense as a Backup Job or a Restore Job: it has an id, a status, and its
own execution log.

```text
Schedule
   │
   └── Backup Job (SUCCESS, full)
              │
              ▼
        Retention Job
              │
              ├── Retention History
              │
              └── drops old Backup References (and their snapshots)

```

A Retention Job is created after a recurring Schedule's full-backup Backup Job completes
successfully. It selects that Schedule's successful full Backup Jobs, keeps the newest
`retention` of them (skipping any that are still a baseline for an existing incremental backup —
see §21), and deletes the rest from the Repository.

A Retention Job's own execution does not change the status of the Backup Job that triggered it,
or of any Backup Job whose data it deletes: deleting a Backup Job's data does not delete the
Backup Job itself, which remains a historical record (see §13-14).

---

# 24. Deleting a Schedule or Cluster

Deleting a Schedule, whether requested by an operator or triggered automatically by a one-shot
Schedule's expiry (see §8), removes every Backup Job the Schedule produced, their Backup History
and Backup References, and the corresponding snapshots in the Repository. This is a cascading
operation, not the removal of a single row.

Because it involves removing data from the Repository, deleting a Schedule is not instantaneous:

1. the request is checked immediately — it is rejected if the Schedule has a Backup Job still in
   progress, or if one of its full backups is currently the baseline of an incremental backup
   belonging to a different Schedule (see §21);
2. once accepted, the removal itself — dropping snapshots from the Repository and deleting the
   Schedule's Jobs, History, and References — runs as its own tracked operation, the same way a
   backup or restore does.

A Restore Job that used one of the deleted Backup Jobs as its source is deleted along with it
(see §27); it exists only as a record of that specific restore, and has no meaning once its
source backup is gone.

Deleting a Cluster follows the same two-step shape. A Cluster can only be deleted once it has no
Schedules at all — enabled or disabled. Once that condition holds, deleting it cascades the same
way: every remaining Backup Job, Retention Job, Restore Job (whether the Cluster was its source
or its target), and Repository belonging to that Cluster is removed.

---

# 25. Restore Job

Restore is a separate operation with its own lifecycle.

A Restore always refers to a specific successful Backup Job.

```text
Backup Job
    │
    ▼
Restore Job

```

One Backup Job can be used for multiple Restores:

```text
Backup Job 123
    │
    ├── Restore Job 1
    ├── Restore Job 2
    └── Restore Job 3

```

---

# 26. Restore Can Use Any Successful Backup Job

The user can select any available, successfully completed Backup Job.

It does not have to be:

* the latest backup,
* the backup created by the most recent Schedule execution,
* the newest backup.

The requirement is that the required data is still available in the Repository and that the Job completed successfully.

---

# 27. Restore Job

A Restore Job represents one specific execution of a data recovery operation.

It should reference:

```text
source_backup_job
target_cluster

```

as well as any other information required to perform the restore.

Its lifecycle is analogous to the Backup Job:

```text
Restore Job
    │
    ├── status
    └── Restore History

```

Unlike a Backup Job, a Restore Job's only purpose is to record that specific restore attempt. A
Restore Job is deleted if its source Backup Job is deleted (see §24), or if either the source or
target Cluster is deleted: once its source backup or either Cluster is gone, keeping the record
serves no purpose.

---

# 28. Restore History

A Restore Job has its own execution log:

```text
Restore Job
    │
    └── 1:N Restore History

```

Example:

```text
10:00:01 RESTORE_STARTED
10:00:02 CONNECTING
10:00:05 DATABASE_RESTORING
10:00:30 TABLE_RESTORED
10:01:00 RESTORE_FINISHED
10:01:01 SUCCESS

```

In case of an error:

```text
10:00:01 RESTORE_STARTED
10:00:02 CONNECTING
10:00:10 ERROR
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

For a recurring backup:

```text
Cluster
   │
   ├── Inventory
   │
   ├── Repository
   │
   └── Schedule
          │
          │ cadence
          ▼
      Backup Job
          │
          ├── Backup History
          │
          └── Backup References
                    │
                    ▼
                Repository
                    │
                    │ retention
                    ▼
              old backup deleted

```

For restore:

```text
Successful Backup Job
        │
        ▼
   Restore Job
        │
        └── Restore History

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
* A Schedule defines the retention.
* Retention is a number of copies.
* Retention is independent for each Schedule.
* `cadence = null` means immediate execution.
* A one-shot Schedule is immutable after creation.
* A one-shot Schedule can be deleted.
* A recurring Schedule generates multiple Backup Jobs.

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

* Retention is defined by the Schedule.
* Only successfully completed Backup Jobs are counted.
* Retention is independent for each Schedule.
* The newest N successful backups are retained.
* Older successful backups are deleted from the Repository.
* Failed Jobs do not affect retention.

### Restore

* Restore is a separate Job.
* A Restore references a specific Backup Job.
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
