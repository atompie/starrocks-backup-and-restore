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

from ...store.models import BackupReference, Job, JobStatus, TableInventory


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
    """Find the most recent successful full-backup Job covering `database_name`, before `before`.

    Resolved via `BackupReference.database_name` rather than a `Job.label` prefix match - see
    `backup_catalog.find_latest_full_backup_job`'s docstring for why (a multi-database Job has
    only one `Job.label`, so it can't be pattern-matched per database).
    """
    return session.scalars(
        select(Job)
        .join(BackupReference, BackupReference.job_id == Job.id)
        .where(
            Job.cluster_id == cluster_id,
            Job.job_type == "backup_full",
            Job.status == JobStatus.SUCCESS.value,
            BackupReference.database_name == database_name,
            Job.finished_at < before,
        )
        .order_by(Job.finished_at.desc())
        .limit(1)
    ).first()


def list_partitions_for_label(session: Session, cluster_id: int, label: str) -> list[tuple[str, str]]:
    job = find_successful_job(session, cluster_id, label)
    if job is None:
        return []

    rows = session.execute(
        select(BackupReference.database_name, BackupReference.table_name)
        .distinct()
        .where(BackupReference.job_id == job.id)
        .order_by(BackupReference.database_name, BackupReference.table_name)
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
    job = find_successful_job(session, cluster_id, label)
    if job is None:
        return []

    return list(
        session.scalars(
            select(BackupReference.partition_name)
            .where(
                BackupReference.job_id == job.id,
                BackupReference.database_name == database_name,
                BackupReference.table_name == table_name,
            )
            .order_by(BackupReference.partition_name)
        )
    )
