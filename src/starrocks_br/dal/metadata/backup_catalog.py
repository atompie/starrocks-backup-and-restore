"""Metadata queries backing backup planning: the backup catalog lookups
(`Job`), inventory-group table memberships (`TableInventory`), and
writing a completed backup's partition manifest (`BackupPartition`).

Backup metadata is resolved from `Job` (`label`/`job_type`/`status`/
`finished_at`) rather than a separate backup catalog - see
add-job-history-log's design.md "The backup catalog moves from
`backup_history` to two new columns on `Job`". `planner.py` decides what
a missing row means (raises the domain exception, formats timestamps for
the cluster timezone); this module only runs the queries/writes.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...store.models import BackupPartition, Job, JobStatus, TableInventory


def find_latest_full_backup_job(session: Session, cluster_id: int, database: str) -> Job | None:
    return session.scalars(
        select(Job)
        .where(
            Job.cluster_id == cluster_id,
            Job.job_type == "backup_full",
            Job.status == JobStatus.SUCCESS.value,
            Job.label.like(f"{database}_%"),
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


def record_partitions(session: Session, cluster_id: int, label: str, partitions: list[dict[str, str]]) -> None:
    """Insert one `BackupPartition` row per entry in `partitions`.

    Args:
        session: SQLite metastore session
        cluster_id: Cluster this backup belongs to
        label: Backup label
        partitions: List of partitions with keys: database, table, partition_name
    """
    if not partitions:
        return

    for partition in partitions:
        composite_key = (
            f"{label}|{partition['database']}|{partition['table']}|{partition['partition_name']}"
        )
        key_hash = hashlib.md5(composite_key.encode("utf-8")).hexdigest()

        session.add(
            BackupPartition(
                cluster_id=cluster_id,
                key_hash=key_hash,
                label=label,
                database_name=partition["database"],
                table_name=partition["table"],
                partition_name=partition["partition_name"],
            )
        )
    session.flush()
