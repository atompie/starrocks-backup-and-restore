"""`RunStatus` queries/writes backing job-slot reservation.

`concurrency.py` decides whether a conflict exists and whether it can be
healed (calling `dal/db/concurrency.py::is_backup_job_stale` for the
StarRocks-side check); this module only runs the `RunStatus` queries and
writes that decision acts on.
"""

from __future__ import annotations

import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...store.models import RunStatus


def active_jobs_for_scope(session: Session, cluster_id: int, scope: str) -> list[tuple[str, str, str]]:
    """Get all active jobs for the given scope."""
    rows = session.execute(
        select(RunStatus.scope, RunStatus.label, RunStatus.state).where(
            RunStatus.cluster_id == cluster_id, RunStatus.state == "ACTIVE"
        )
    ).all()
    return [tuple(row) for row in rows if row[0] == scope]


def insert_active_job(session: Session, cluster_id: int, scope: str, label: str) -> None:
    session.add(RunStatus(cluster_id=cluster_id, scope=scope, label=label, state="ACTIVE"))
    session.flush()


def cancel_stale_job(session: Session, cluster_id: int, scope: str, label: str) -> None:
    row = session.scalars(
        select(RunStatus).where(
            RunStatus.cluster_id == cluster_id,
            RunStatus.scope == scope,
            RunStatus.label == label,
            RunStatus.state == "ACTIVE",
        )
    ).one_or_none()
    if row is None:
        return
    row.state = "CANCELLED"
    row.finished_at = datetime.datetime.now(datetime.timezone.utc)
    session.flush()


def complete_job(session: Session, cluster_id: int, scope: str, label: str, final_state: str) -> None:
    row = session.scalars(
        select(RunStatus).where(
            RunStatus.cluster_id == cluster_id, RunStatus.scope == scope, RunStatus.label == label
        )
    ).one_or_none()
    if row is None:
        return
    row.state = final_state
    row.finished_at = datetime.datetime.now(datetime.timezone.utc)
    session.flush()
