"""Schedule CRUD and the run-due dispatch queries.

`advance_next_run_at` is a conditional `UPDATE ... WHERE next_run_at ==
previously_due_at` - see `commands/schedules.py::run_due_schedules`'s
docstring for why: it's what makes advancing a due schedule idempotent
under concurrent run-due calls without a separate lock table. This module
only runs the statement and reports how many rows it matched; deciding
what a zero-row match means is `run_due_schedules`'s job.
"""

from __future__ import annotations

import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ...store.models import Schedule


def create(
    db: Session,
    *,
    cluster_id: int,
    job_type: str,
    inventory_group_id: int,
    repository: str,
    cadence: str | None,
    backend: str | None,
    enabled: bool,
    next_run_at: datetime.datetime | None,
    retention: int | None = None,
    expire_after_days: int | None = None,
) -> Schedule:
    schedule = Schedule(
        cluster_id=cluster_id,
        job_type=job_type,
        inventory_group_id=inventory_group_id,
        repository=repository,
        cadence=cadence,
        backend=backend,
        enabled=enabled,
        next_run_at=next_run_at,
        retention=retention,
        expire_after_days=expire_after_days,
    )
    db.add(schedule)
    db.flush()
    db.refresh(schedule)
    return schedule


def list_for_cluster(db: Session, cluster_id: int) -> list[Schedule]:
    return list(
        db.scalars(
            select(Schedule).where(Schedule.cluster_id == cluster_id).order_by(Schedule.id)
        ).all()
    )


def get(db: Session, cluster_id: int, schedule_id: int) -> Schedule | None:
    schedule = db.get(Schedule, schedule_id)
    if schedule is None or schedule.cluster_id != cluster_id:
        return None
    return schedule


def update_fields(db: Session, schedule: Schedule, updates: dict) -> Schedule:
    for field, value in updates.items():
        setattr(schedule, field, value)
    db.flush()
    db.refresh(schedule)
    return schedule


def delete(db: Session, schedule: Schedule) -> None:
    db.delete(schedule)


def due_schedules(db: Session, now: datetime.datetime) -> list[Schedule]:
    """A one-shot schedule (`cadence IS NULL`) is never due - it runs exactly once, at creation.

    Its `next_run_at` is also `NULL`, which `next_run_at <= now` already excludes on any
    standard-SQL backend, but `cadence IS NOT NULL` is kept explicit rather than relying on that.
    """
    return list(
        db.scalars(
            select(Schedule).where(
                Schedule.enabled.is_(True),
                Schedule.cadence.is_not(None),
                Schedule.next_run_at <= now,
            )
        ).all()
    )


def advance_next_run_at(
    db: Session,
    schedule_id: int,
    previously_due_at: datetime.datetime,
    new_next_run_at: datetime.datetime,
) -> int:
    """Conditionally advance `next_run_at`; returns the number of rows matched (0 or 1)."""
    result = db.execute(
        update(Schedule)
        .where(Schedule.id == schedule_id, Schedule.next_run_at == previously_due_at)
        .values(next_run_at=new_next_run_at)
    )
    return result.rowcount
