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
from ..exceptions import InvalidCadenceError, InvalidScheduleFieldsError, ScheduleImmutableError
from ..store.models import Cluster, Schedule
from .jobs import submit_job


def compute_next_run_at(cadence: str, after: datetime.datetime | None = None) -> datetime.datetime:
    base = after or datetime.datetime.now(datetime.timezone.utc)
    try:
        itr = croniter(cadence, base)
        return itr.get_next(datetime.datetime)
    except (CroniterBadCronError, ValueError) as e:
        raise InvalidCadenceError(cadence, str(e)) from e


def _validate_schedule_shape(
    job_type: str,
    cadence: str | None,
    retention: int | None,
    expire_after_days: int | None,
) -> None:
    """Enforce the retention/expiry rules from `SPEC.md` §8-11 and decision Q3.

    - a one-shot schedule (`cadence is None`) is rejected if `job_type` is `backup_incremental`
      (Q3: incrementals only come from recurring schedules);
    - `retention` is required for a recurring `backup_full` schedule, forbidden otherwise
      (recurring incremental, and both one-shot cases - it never applies to a schedule that
      isn't a recurring full backup);
    - `expire_after_days` is only accepted for a one-shot schedule.
    """
    is_one_shot = cadence is None

    if is_one_shot and job_type == "backup_incremental":
        raise InvalidScheduleFieldsError(
            "A one-shot schedule must be a full backup; incremental backups only come from "
            "recurring schedules"
        )

    retention_required = not is_one_shot and job_type == "backup_full"
    if retention_required and retention is None:
        raise InvalidScheduleFieldsError(
            "'retention' is required for a recurring full-backup schedule"
        )
    if not retention_required and retention is not None:
        raise InvalidScheduleFieldsError(
            "'retention' only applies to a recurring full-backup schedule"
        )

    if not is_one_shot and expire_after_days is not None:
        raise InvalidScheduleFieldsError("'expire_after_days' only applies to a one-shot schedule")


def create_schedule(
    db: Session,
    cluster: Cluster,
    *,
    job_type: str,
    inventory_group_id: int,
    repository: str,
    cadence: str | None,
    backend: str | None,
    enabled: bool,
    retention: int | None = None,
    expire_after_days: int | None = None,
) -> Schedule:
    """Create a recurring or one-shot (`cadence is None`) schedule.

    A one-shot schedule immediately submits exactly one job through the same
    `submit_job` path `run_due_schedules` uses, recording it as the schedule's
    most recent run the same way - see design.md's "Job.schedule_id and
    Job.baseline_job_id are threaded very differently" for why this mirrors
    `run_due_schedules` rather than inventing a second submission shape.
    """
    _validate_schedule_shape(job_type, cadence, retention, expire_after_days)

    next_run_at = compute_next_run_at(cadence) if cadence is not None else None
    schedule = schedules_dal.create(
        db,
        cluster_id=cluster.id,
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

    if cadence is None:
        job = submit_job(
            db,
            cluster,
            job_type,
            {"group_id": inventory_group_id, "repository": repository},
            backend,
            schedule_id=schedule.id,
        )
        schedule.last_run_job_id = job.id
        db.flush()

    return schedule


def list_schedules(db: Session, cluster_id: int) -> list[Schedule]:
    return schedules_dal.list_for_cluster(db, cluster_id)


def get_schedule(db: Session, cluster_id: int, schedule_id: int) -> Schedule | None:
    return schedules_dal.get(db, cluster_id, schedule_id)


def update_schedule(db: Session, schedule: Schedule, updates: dict) -> Schedule:
    """Apply `updates` to `schedule`, recomputing `next_run_at` when `cadence` changes.

    A one-shot schedule (`schedule.cadence is None`) is immutable - any update to it is
    rejected regardless of which field it targets (SPEC.md §8). Converting a recurring
    schedule's `cadence` to null (turning it into a one-shot after the fact) is rejected too;
    shot-type is fixed at creation. The merged result must still satisfy the same
    retention/expire_after_days rules creation does.
    """
    if schedule.cadence is None:
        raise ScheduleImmutableError(schedule.id)

    updates = dict(updates)
    if "cadence" in updates and updates["cadence"] is None:
        raise InvalidScheduleFieldsError(
            "Cannot convert a recurring schedule to one-shot by setting 'cadence' to null"
        )

    merged_job_type = updates.get("job_type", schedule.job_type)
    merged_cadence = updates.get("cadence", schedule.cadence)
    merged_retention = updates.get("retention", schedule.retention)
    merged_expire_after_days = updates.get("expire_after_days", schedule.expire_after_days)
    _validate_schedule_shape(merged_job_type, merged_cadence, merged_retention, merged_expire_after_days)

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
            schedule_id=schedule.id,
        )
        schedule.last_run_job_id = job.id
        triggered_job_ids.append(job.id)

    session.flush()
    return triggered_job_ids, len(triggered_job_ids)
