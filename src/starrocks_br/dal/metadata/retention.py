"""Metadata queries backing schedule-scoped retention (SPEC.md §10, §11, §21).

A recurring full-backup schedule's pool is its own successful full backups whose data has not
been deleted. The newest `schedule.retention` are kept; the rest are droppable unless protected
as the baseline of an incremental backup or as the source of an open restore. Dropping a backup
only marks its `BackupReference` rows deleted: the `Job` and its history stay.
"""

from __future__ import annotations

import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ...store.models import BackupReference, Job, JobStatus, JobType, Schedule

_ACTIVE_STATUSES = [JobStatus.PENDING.value, JobStatus.RUNNING.value]
_INCREMENTAL_BASELINE_STATUSES = [*_ACTIVE_STATUSES, JobStatus.SUCCESS.value]


def schedules_subject_to_retention(session: Session) -> list[Schedule]:
    """Recurring full-backup schedules that are not pending deletion, oldest first."""
    return list(
        session.scalars(
            select(Schedule)
            .where(
                Schedule.job_type == JobType.BACKUP_FULL.value,
                Schedule.cadence.is_not(None),
                Schedule.retention.is_not(None),
                Schedule.deletion_requested_at.is_(None),
            )
            .order_by(Schedule.id)
        ).all()
    )


def eligible_full_backups(session: Session, schedule_id: int) -> list[Job]:
    """The schedule's pool, newest first: successful full backups that still have live data."""
    has_live_reference = (
        select(BackupReference.id)
        .where(BackupReference.job_id == Job.id, BackupReference.deleted_at.is_(None))
        .exists()
    )
    return list(
        session.scalars(
            select(Job)
            .where(
                Job.schedule_id == schedule_id,
                Job.job_type == JobType.BACKUP_FULL.value,
                Job.status == JobStatus.SUCCESS.value,
                has_live_reference,
            )
            .order_by(Job.finished_at.desc(), Job.id.desc())
        ).all()
    )


def active_incremental_baseline_ids(session: Session, cluster_id: int) -> set[int]:
    """Baselines of every `PENDING`, `RUNNING` or `SUCCESS` incremental backup on the cluster."""
    return set(
        session.scalars(
            select(Job.baseline_job_id).where(
                Job.cluster_id == cluster_id,
                Job.job_type == JobType.BACKUP_INCREMENTAL.value,
                Job.status.in_(_INCREMENTAL_BASELINE_STATUSES),
                Job.baseline_job_id.is_not(None),
            )
        ).all()
    )


def active_restore_source_ids(session: Session, cluster_id: int) -> set[int]:
    """Sources of `PENDING`/`RUNNING` restores on the cluster, plus the baselines of those sources."""
    sources = list(
        session.scalars(
            select(Job.source_backup_job_id).where(
                Job.cluster_id == cluster_id,
                Job.job_type == JobType.RESTORE.value,
                Job.status.in_(_ACTIVE_STATUSES),
                Job.source_backup_job_id.is_not(None),
            )
        ).all()
    )
    if not sources:
        return set()
    baselines = session.scalars(
        select(Job.baseline_job_id).where(Job.id.in_(sources), Job.baseline_job_id.is_not(None))
    ).all()
    return set(sources) | set(baselines)


def droppable_backups(session: Session, schedule: Schedule) -> list[Job]:
    """Backups outside the newest `retention` of the schedule's pool that nothing protects, newest first."""
    if schedule.retention is None or schedule.job_type != JobType.BACKUP_FULL.value:
        return []
    pool = eligible_full_backups(session, schedule.id)
    candidates = pool[schedule.retention :]
    if not candidates:
        return []
    protected = active_incremental_baseline_ids(session, schedule.cluster_id) | active_restore_source_ids(
        session, schedule.cluster_id
    )
    return [job for job in candidates if job.id not in protected]


def live_snapshots_for_job(session: Session, job_id: int) -> list[tuple[str, str]]:
    """Distinct `(repository, snapshot_label)` of the backup's references that are not yet deleted."""
    rows = session.execute(
        select(BackupReference.repository, BackupReference.snapshot_label)
        .distinct()
        .where(BackupReference.job_id == job_id, BackupReference.deleted_at.is_(None))
        .order_by(BackupReference.repository, BackupReference.snapshot_label)
    ).all()
    return [(row[0], row[1]) for row in rows]


def mark_references_deleted(session: Session, job_id: int, now: datetime.datetime) -> int:
    """Stamp the backup's live references as deleted; returns how many rows changed."""
    result = session.execute(
        update(BackupReference)
        .where(BackupReference.job_id == job_id, BackupReference.deleted_at.is_(None))
        .values(deleted_at=now)
    )
    session.flush()
    return result.rowcount
