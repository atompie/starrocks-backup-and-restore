"""Schedule CRUD, cadence validation, and the run-due dispatch loop.

Per design.md Decision 5: `run_due_schedules` advances each due schedule's
`next_run_at` with a conditional UPDATE (WHERE next_run_at <= now, matching
the value already read) *before* submitting its job, in the same
transaction. A second concurrent call only sees a row it can no longer
conditionally advance and skips it - this is what makes run-due idempotent
per occurrence without a separate lock table. The conditional UPDATE itself
lives in `dal/metadata/schedules.py::advance_next_run_at`; this module only
decides what a 0-row match means (skip).
"""

import datetime

from croniter import CroniterBadCronError, croniter
from sqlalchemy.orm import Session

from ..dal.metadata import clusters as clusters_dal
from ..dal.metadata import schedules as schedules_dal
from ..exceptions import InvalidCadenceError
from ..store.models import Schedule
from .jobs import submit_job


def compute_next_run_at(cadence: str, after: datetime.datetime | None = None) -> datetime.datetime:
    base = after or datetime.datetime.now(datetime.timezone.utc)
    try:
        itr = croniter(cadence, base)
        return itr.get_next(datetime.datetime)
    except (CroniterBadCronError, ValueError) as e:
        raise InvalidCadenceError(cadence, str(e)) from e


def create_schedule(
    db: Session,
    *,
    cluster_id: int,
    job_type: str,
    inventory_group_id: int,
    repository: str,
    cadence: str,
    backend: str | None,
    enabled: bool,
) -> Schedule:
    next_run_at = compute_next_run_at(cadence)
    return schedules_dal.create(
        db,
        cluster_id=cluster_id,
        job_type=job_type,
        inventory_group_id=inventory_group_id,
        repository=repository,
        cadence=cadence,
        backend=backend,
        enabled=enabled,
        next_run_at=next_run_at,
    )


def list_schedules(db: Session, cluster_id: int) -> list[Schedule]:
    return schedules_dal.list_for_cluster(db, cluster_id)


def get_schedule(db: Session, cluster_id: int, schedule_id: int) -> Schedule | None:
    return schedules_dal.get(db, cluster_id, schedule_id)


def update_schedule(db: Session, schedule: Schedule, updates: dict) -> Schedule:
    """Apply `updates` to `schedule`, recomputing `next_run_at` when `cadence` changes."""
    updates = dict(updates)
    cadence_changed = "cadence" in updates
    updated = schedules_dal.update_fields(db, schedule, updates)
    if cadence_changed:
        updated = schedules_dal.update_fields(
            db, updated, {"next_run_at": compute_next_run_at(updated.cadence)}
        )
    return updated


def delete_schedule(db: Session, schedule: Schedule) -> None:
    schedules_dal.delete(db, schedule)


def run_due_schedules(session: Session, now: datetime.datetime) -> tuple[list[int], int]:
    due = schedules_dal.due_schedules(session, now)

    triggered_job_ids: list[int] = []

    for schedule in due:
        previously_due_at = schedule.next_run_at
        new_next_run_at = compute_next_run_at(schedule.cadence, after=now)

        rowcount = schedules_dal.advance_next_run_at(
            session, schedule.id, previously_due_at, new_next_run_at
        )
        if rowcount == 0:
            # Another concurrent run-due call already advanced this schedule
            # past this due occurrence - skip to stay idempotent.
            continue

        cluster = clusters_dal.get(session, schedule.cluster_id)
        job = submit_job(
            session,
            cluster,
            schedule.job_type,
            {"group_id": schedule.inventory_group_id, "repository": schedule.repository},
            schedule.backend,
        )
        schedule.last_run_job_id = job.id
        triggered_job_ids.append(job.id)

    session.flush()
    return triggered_job_ids, len(triggered_job_ids)
