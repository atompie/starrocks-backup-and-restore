"""Metadata queries backing restore lineage/manifest lookups.

Backup lineage/repository is resolved from `Job` (`label`/`job_type`/
`status`/`finished_at`/`repository`) rather than a separate backup
catalog - see add-job-history-log's design.md "The backup catalog moves
from `backup_history` to two new columns on `Job`". `restore.py` decides
what a missing row means (raises the domain exception); this module only
runs the queries.
"""

from __future__ import annotations

import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...store.models import BackupPartition, Job, JobStatus, TableInventory


def find_successful_job(session: Session, cluster_id: int, label: str) -> Job | None:
    return session.scalars(
        select(Job).where(
            Job.cluster_id == cluster_id,
            Job.label == label,
            Job.status == JobStatus.SUCCESS.value,
        )
    ).first()


def find_latest_full_backup_before(
    session: Session, cluster_id: int, database_name: str, before: datetime.datetime
) -> Job | None:
    return session.scalars(
        select(Job)
        .where(
            Job.cluster_id == cluster_id,
            Job.job_type == "backup_full",
            Job.status == JobStatus.SUCCESS.value,
            Job.label.like(f"{database_name}_%"),
            Job.finished_at < before,
        )
        .order_by(Job.finished_at.desc())
        .limit(1)
    ).first()


def list_partitions_for_label(session: Session, cluster_id: int, label: str) -> list[tuple[str, str]]:
    rows = session.execute(
        select(BackupPartition.database_name, BackupPartition.table_name)
        .distinct()
        .where(BackupPartition.cluster_id == cluster_id, BackupPartition.label == label)
        .order_by(BackupPartition.database_name, BackupPartition.table_name)
    ).all()
    return [(row[0], row[1]) for row in rows]


def list_group_table_memberships(session: Session, cluster_id: int, group_id: int) -> list[tuple[str, str]]:
    rows = session.execute(
        select(TableInventory.database_name, TableInventory.table_name).where(
            TableInventory.cluster_id == cluster_id, TableInventory.inventory_group_id == group_id
        )
    ).all()
    return [(row[0], row[1]) for row in rows]


def list_partition_names(
    session: Session, cluster_id: int, label: str, database_name: str, table_name: str
) -> list[str]:
    return list(
        session.scalars(
            select(BackupPartition.partition_name)
            .where(
                BackupPartition.cluster_id == cluster_id,
                BackupPartition.label == label,
                BackupPartition.database_name == database_name,
                BackupPartition.table_name == table_name,
            )
            .order_by(BackupPartition.partition_name)
        )
    )
