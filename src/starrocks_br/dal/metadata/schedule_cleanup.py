"""Metadata queries backing schedule deletion validation and the `schedule_cleanup` job.

See design.md's "Mark accepted schedules as pending deletion" and "Drop snapshots outside
metadata transactions": deletion validation, marking, and cleanup all resolve a schedule's
backup jobs, dependent restores, and incremental-baseline dependents from `Job`/
`BackupReference` rather than a separate cleanup-tracking table.
"""

from __future__ import annotations

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from ...store.models import BackupReference, Job, JobStatus, JobType

_BACKUP_JOB_TYPES = [JobType.BACKUP_FULL.value, JobType.BACKUP_INCREMENTAL.value]
_ACTIVE_STATUSES = [JobStatus.PENDING.value, JobStatus.RUNNING.value]


def active_backup_jobs(session: Session, schedule_id: int) -> list[Job]:
    """PENDING/RUNNING backup jobs submitted by this schedule (never the cleanup job itself)."""
    return list(
        session.scalars(
            select(Job).where(
                Job.schedule_id == schedule_id,
                Job.job_type.in_(_BACKUP_JOB_TYPES),
                Job.status.in_(_ACTIVE_STATUSES),
            )
        ).all()
    )


def backup_job_ids_for_schedule(session: Session, schedule_id: int) -> list[int]:
    return list(
        session.scalars(
            select(Job.id).where(Job.schedule_id == schedule_id, Job.job_type.in_(_BACKUP_JOB_TYPES))
        ).all()
    )


def active_restores_for_schedule(session: Session, schedule_id: int) -> list[Job]:
    """PENDING/RUNNING restores whose source backup was submitted by this schedule."""
    backup_ids = backup_job_ids_for_schedule(session, schedule_id)
    if not backup_ids:
        return []
    return list(
        session.scalars(
            select(Job).where(
                Job.job_type == JobType.RESTORE.value,
                Job.status.in_(_ACTIVE_STATUSES),
                Job.source_backup_job_id.in_(backup_ids),
            )
        ).all()
    )


def incremental_baseline_conflict(session: Session, schedule_id: int) -> Job | None:
    """A successful incremental backup from another schedule that uses one of this
    schedule's full backups as its baseline, if any - blocks deletion per design.md.
    """
    backup_ids = backup_job_ids_for_schedule(session, schedule_id)
    if not backup_ids:
        return None
    return session.scalars(
        select(Job).where(
            Job.job_type == JobType.BACKUP_INCREMENTAL.value,
            Job.baseline_job_id.in_(backup_ids),
            or_(Job.schedule_id.is_(None), Job.schedule_id != schedule_id),
        )
    ).first()


def distinct_references_for_schedule(session: Session, schedule_id: int) -> list[tuple[str, str]]:
    """Distinct (repository, snapshot_label) pairs referenced by this schedule's backup jobs."""
    backup_ids = backup_job_ids_for_schedule(session, schedule_id)
    if not backup_ids:
        return []
    rows = session.execute(
        select(BackupReference.repository, BackupReference.snapshot_label)
        .distinct()
        .where(BackupReference.job_id.in_(backup_ids))
    ).all()
    return [(row[0], row[1]) for row in rows]


def delete_schedule_backup_jobs(session: Session, schedule_id: int) -> None:
    """Delete this schedule's backup Jobs, cascading their histories, references, and any
    dependent Restore Jobs (via `source_backup_job_id` ON DELETE CASCADE). The schedule's own
    `schedule_cleanup` job is never a backup job, so it is never matched here.
    """
    session.execute(
        delete(Job).where(Job.schedule_id == schedule_id, Job.job_type.in_(_BACKUP_JOB_TYPES))
    )
    session.flush()


def find_active_cleanup_job(session: Session, schedule_id: int) -> Job | None:
    return session.scalars(
        select(Job).where(
            Job.schedule_id == schedule_id,
            Job.job_type == JobType.SCHEDULE_CLEANUP.value,
            Job.status.in_(_ACTIVE_STATUSES),
        )
    ).first()
