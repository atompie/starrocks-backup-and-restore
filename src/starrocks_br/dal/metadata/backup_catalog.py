"""Metadata queries backing backup planning: the backup catalog lookups
(`Job`), inventory-group table memberships (`TableInventory`), and
writing a completed backup's reference manifest (`BackupReference`).

Backup metadata is resolved from `Job` (`label`/`job_type`/`status`/
`finished_at`) rather than a separate backup catalog - see
add-job-history-log's design.md "The backup catalog moves from
`backup_history` to two new columns on `Job`". `planner.py` decides what
a missing row means (raises the domain exception, formats timestamps for
the cluster timezone); this module only runs the queries/writes.

`BackupReference` rows are written only once a job's backup succeeds
(`job_id`-keyed, per backup-references/design.md), never before
execution - a failed job has no references (SPEC.md §16).
"""

from __future__ import annotations

import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...store.models import BackupReference, Job, JobStatus, TableInventory


def find_latest_full_backup_job(session: Session, cluster_id: int, database: str) -> Job | None:
    """Find the most recent successful full-backup Job covering `database`.

    Resolved via `BackupReference.database_name` rather than a `Job.label` prefix match: a Job
    covering multiple databases (a multi-database inventory group, SPEC.md §5) has only one
    `Job.label`, so label-prefix matching can't tell which of its databases a label belongs to.
    Joining through the job-scoped reference rows works regardless of how many databases a Job
    covers (see backup-references/design.md).
    """
    return session.scalars(
        select(Job)
        .join(BackupReference, BackupReference.job_id == Job.id)
        .where(
            Job.cluster_id == cluster_id,
            Job.job_type == "backup_full",
            Job.status == JobStatus.SUCCESS.value,
            BackupReference.database_name == database,
        )
        .order_by(Job.finished_at.desc())
        .limit(1)
    ).first()


def find_successful_job_by_label(session: Session, cluster_id: int, label: str) -> Job | None:
    return session.scalars(
        select(Job).where(
            Job.cluster_id == cluster_id,
            Job.label == label,
            Job.status == JobStatus.SUCCESS.value,
        )
    ).first()


def list_group_table_memberships(session: Session, cluster_id: int, group_id: int) -> list[TableInventory]:
    return list(
        session.scalars(
            select(TableInventory)
            .where(TableInventory.cluster_id == cluster_id, TableInventory.inventory_group_id == group_id)
            .order_by(TableInventory.database_name, TableInventory.table_name)
        )
    )


def record_references(
    session: Session,
    job_id: int,
    repository: str,
    snapshot_label: str,
    snapshot_timestamp: datetime.datetime,
    partitions: list[dict[str, str]],
) -> None:
    """Insert one `BackupReference` row per entry in `partitions`.

    Called only once a backup job's StarRocks operation has reached `FINISHED` - a job that
    fails writes no references (SPEC.md §16).

    Args:
        session: SQLite metastore session
        job_id: The backup Job these references belong to
        repository: Repository name the snapshot was written to
        snapshot_label: StarRocks snapshot/backup label
        snapshot_timestamp: When the backup finished
        partitions: List of partitions with keys: database, table, partition_name
    """
    if not partitions:
        return

    for partition in partitions:
        session.add(
            BackupReference(
                job_id=job_id,
                repository=repository,
                snapshot_label=snapshot_label,
                snapshot_timestamp=snapshot_timestamp,
                database_name=partition["database"],
                table_name=partition["table"],
                partition_name=partition["partition_name"],
            )
        )
    session.flush()
