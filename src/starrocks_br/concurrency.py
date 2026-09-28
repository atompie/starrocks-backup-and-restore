from typing import Literal

from sqlalchemy.orm import Session

from . import exceptions, logger
from .dal.db import concurrency as concurrency_dal
from .dal.metadata import concurrency as concurrency_metadata_dal


def reserve_job_slot(db, session: Session, cluster_id: int, scope: str, label: str) -> None:
    """Reserve a job slot in the run_status table to prevent overlapping jobs.

    We consider any row with state='ACTIVE' for the same scope as a conflict.
    However, we implement self-healing logic to automatically clean up stale locks.
    """
    active_jobs = _get_active_jobs_for_scope(session, cluster_id, scope)

    if not active_jobs:
        _insert_new_job(session, cluster_id, scope, label)
        return

    _handle_active_job_conflicts(db, session, cluster_id, scope, active_jobs)

    _insert_new_job(session, cluster_id, scope, label)


def _get_active_jobs_for_scope(session: Session, cluster_id: int, scope: str) -> list[tuple[str, str, str]]:
    """Get all active jobs for the given scope."""
    return concurrency_metadata_dal.active_jobs_for_scope(session, cluster_id, scope)


def _handle_active_job_conflicts(
    db, session: Session, cluster_id: int, scope: str, active_jobs: list[tuple[str, str, str]]
) -> None:
    """Handle conflicts with active jobs, cleaning up stale ones where possible."""
    for active_scope, active_label, _ in active_jobs:
        if _can_heal_stale_job(db, active_scope, active_label):
            _cleanup_stale_job(session, cluster_id, active_scope, active_label)
            logger.success(f"Cleaned up stale backup job: {active_label}")
        else:
            _raise_concurrency_conflict(scope, active_jobs)


def _can_heal_stale_job(db, scope: str, label: str) -> bool:
    """Check if a stale job can be healed (only for backup jobs)."""
    if scope != "backup":
        return False

    return concurrency_dal.is_backup_job_stale(db, label)


def _raise_concurrency_conflict(scope: str, active_jobs: list[tuple[str, str, str]]) -> None:
    """Raise a concurrency conflict error with helpful message."""
    raise exceptions.ConcurrencyConflictError(scope, active_jobs)


def _insert_new_job(session: Session, cluster_id: int, scope: str, label: str) -> None:
    """Insert a new active job record."""
    concurrency_metadata_dal.insert_active_job(session, cluster_id, scope, label)


def _cleanup_stale_job(session: Session, cluster_id: int, scope: str, label: str) -> None:
    """Clean up a stale job by updating its state to CANCELLED."""
    concurrency_metadata_dal.cancel_stale_job(session, cluster_id, scope, label)


def complete_job_slot(
    session: Session,
    cluster_id: int,
    scope: str,
    label: str,
    final_state: Literal["FINISHED", "FAILED", "CANCELLED"],
) -> None:
    """Complete job slot and persist final state.

    Simple approach: update the same row by scope/label.
    """
    concurrency_metadata_dal.complete_job(session, cluster_id, scope, label, final_state)
