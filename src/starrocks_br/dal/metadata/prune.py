"""Metadata queries backing pruning: the successful-backup catalog lookup
and cleanup of a pruned snapshot's catalog entry/partition manifest.

Backup metadata is resolved from `Job` (`label`/`repository`/`status`/
`finished_at`) rather than a separate backup catalog - see
add-job-history-log's design.md "The backup catalog moves from
`backup_history` to two new columns on `Job`".
"""

from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ...store.models import BackupReference, Job, JobStatus, TableInventory


def get_successful_backups(session: Session, cluster_id: int, group: int) -> list[dict]:
    """Get all successful backups belonging to an inventory group's backup catalog.

    Per specs/api-job-execution "Prune requests specify exactly one pruning
    strategy", prune is always scoped to one inventory group - there is no
    cluster-level default repository to filter by any more, so each
    returned backup carries its own recorded `repository` instead.

    Returns:
        List of backup records as dicts with keys: label, finished_at, repository, inventory_group_id
    """
    rows = session.execute(
        select(
            Job.label,
            Job.finished_at,
            Job.repository,
            TableInventory.inventory_group_id,
        )
        .distinct()
        .join(BackupReference, BackupReference.job_id == Job.id)
        .join(
            TableInventory,
            and_(
                TableInventory.database_name == BackupReference.database_name,
                or_(TableInventory.table_name == BackupReference.table_name, TableInventory.table_name == "*"),
                TableInventory.cluster_id == cluster_id,
            ),
        )
        .where(
            Job.cluster_id == cluster_id,
            Job.status == JobStatus.SUCCESS.value,
            TableInventory.inventory_group_id == group,
        )
        .order_by(Job.finished_at.asc())
    ).all()

    return [
        {
            "label": label,
            "finished_at": str(finished_at),
            "repository": repository,
            "inventory_group_id": inventory_group_id,
        }
        for label, finished_at, repository, inventory_group_id in rows
    ]


def cleanup_backup_history(session: Session, cluster_id: int, snapshot_label: str) -> None:
    """Remove a pruned snapshot's catalog entry and reference manifest.

    Deletes the `Job` row for this backup label rather than a
    `backup_history` row - that table is now an immutable execution log
    (see add-job-history-log's design.md "Pruning a snapshot deletes its
    `Job` row instead of a `backup_history` row"). Deleting the `Job` row
    cascades to its `backup_history` and `backup_references` rows via the
    existing `ON DELETE CASCADE`, removing the pruned backup's full
    execution log and reference manifest along with its catalog entry.
    """
    job = session.scalars(
        select(Job).where(Job.cluster_id == cluster_id, Job.label == snapshot_label)
    ).first()
    if job is not None:
        session.delete(job)
    session.flush()
