"""The single implementation of cadence validation and the run-due dispatch loop.

Per design.md Decision 5: `run_due_schedules` advances each due schedule's
`next_run_at` with a conditional UPDATE (WHERE next_run_at <= now, matching
the value already read) *before* submitting its job, in the same
transaction. A second concurrent call only sees a row it can no longer
conditionally advance and skips it - this is what makes run-due idempotent
per occurrence without a separate lock table.
"""

import datetime

from croniter import CroniterBadCronError, croniter
from sqlalchemy import update
from sqlalchemy.orm import Session

from ..exceptions import InvalidCadenceError
from ..store.models import Cluster, Schedule
from .jobs import submit_job


def compute_next_run_at(cadence: str, after: datetime.datetime | None = None) -> datetime.datetime:
    base = after or datetime.datetime.now(datetime.timezone.utc)
    try:
        itr = croniter(cadence, base)
        return itr.get_next(datetime.datetime)
    except (CroniterBadCronError, ValueError) as e:
        raise InvalidCadenceError(cadence, str(e)) from e


def run_due_schedules(session: Session, now: datetime.datetime) -> tuple[list[int], int]:
    due_schedules = (
        session.query(Schedule)
        .filter(Schedule.enabled.is_(True), Schedule.next_run_at <= now)
        .all()
    )

    triggered_job_ids: list[int] = []

    for schedule in due_schedules:
        previously_due_at = schedule.next_run_at
        new_next_run_at = compute_next_run_at(schedule.cadence, after=now)

        result = session.execute(
            update(Schedule)
            .where(Schedule.id == schedule.id, Schedule.next_run_at == previously_due_at)
            .values(next_run_at=new_next_run_at)
        )
        if result.rowcount == 0:
            # Another concurrent run-due call already advanced this schedule
            # past this due occurrence - skip to stay idempotent.
            continue

        cluster = session.get(Cluster, schedule.cluster_id)
        job = submit_job(
            session,
            cluster,
            schedule.job_type,
            {"group_id": schedule.inventory_group_id},
            schedule.backend,
        )
        schedule.last_run_job_id = job.id
        triggered_job_ids.append(job.id)

    session.flush()
    return triggered_job_ids, len(triggered_job_ids)
