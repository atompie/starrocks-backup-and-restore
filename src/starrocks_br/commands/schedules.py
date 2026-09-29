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

from collections.abc import Callable
from typing import Any

from croniter import CroniterBadCronError, croniter
from sqlalchemy.orm import Session

from .. import logger, prune
from ..dal.metadata import clusters as clusters_dal
from ..dal.metadata import schedule_cleanup as schedule_cleanup_dal
from ..dal.metadata import schedules as schedules_dal
from ..exceptions import (
    InvalidCadenceError,
    InvalidScheduleFieldsError,
    ScheduleHasActiveJobError,
    ScheduleHasActiveRestoreError,
    ScheduleHasIncrementalBaselineError,
    ScheduleImmutableError,
    SchedulePendingDeletionError,
)
from ..store.models import Cluster, Job, JobType, Schedule
from ..store.session import session_scope
from ._shared import connect, ensure_ready
from .jobs import submit_job

OnProgress = Callable[[dict], None] | None


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


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

    if schedule.deletion_requested_at is not None:
        raise SchedulePendingDeletionError(schedule.id)

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


def delete_schedule(db: Session, schedule: Schedule) -> Job:
    """Validate, then accept a schedule deletion as a tracked `schedule_cleanup` job.

    See design.md "Mark accepted schedules as pending deletion": validation, marking the
    schedule, and creating the cleanup job all happen against the same session, which
    `submit_job`'s eager commit (via `jobs_dal.create_job`) persists atomically. Repeating the
    call while a cleanup job is still PENDING/RUNNING returns that same job instead of
    submitting a second one; once a cleanup job has failed, validation and job creation run
    again (they are safe to repeat - due dispatch and restore submission already stopped
    producing new active jobs against a pending-deletion schedule).
    """
    existing_cleanup = schedule_cleanup_dal.find_active_cleanup_job(db, schedule.id)
    if existing_cleanup is not None:
        return existing_cleanup

    if schedule_cleanup_dal.active_backup_jobs(db, schedule.id):
        raise ScheduleHasActiveJobError(schedule.id)

    if schedule_cleanup_dal.active_restores_for_schedule(db, schedule.id):
        raise ScheduleHasActiveRestoreError(schedule.id)

    if schedule_cleanup_dal.incremental_baseline_conflict(db, schedule.id) is not None:
        raise ScheduleHasIncrementalBaselineError(schedule.id)

    if schedule.deletion_requested_at is None:
        schedules_dal.mark_pending_deletion(db, schedule, _utcnow())

    cluster = clusters_dal.get(db, schedule.cluster_id)
    return submit_job(
        db,
        cluster,
        JobType.SCHEDULE_CLEANUP.value,
        {"schedule_id": schedule.id},
        schedule.backend,
        schedule_id=schedule.id,
    )


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


def expire_due_schedules(db: Session, now: datetime.datetime) -> list[int]:
    """Submit (or retry) `schedule_cleanup` for every one-shot schedule due for expiry.

    Shared entry point for the scheduler tick (design.md "Route retirement, test migration,
    and API response"). Reuses `delete_schedule`'s validation, so an expiry blocked by an
    active job or an incremental-baseline dependency is simply logged and left for a later
    tick, exactly like a blocked operator deletion would be.
    """
    due_for_expiry = schedules_dal.one_shot_schedules_due_for_expiry_cleanup(db, now)

    cleanup_job_ids: list[int] = []
    for schedule in due_for_expiry:
        try:
            job = delete_schedule(db, schedule)
        except (
            ScheduleHasActiveJobError,
            ScheduleHasActiveRestoreError,
            ScheduleHasIncrementalBaselineError,
        ) as e:
            logger.info(f"Schedule {schedule.id} expiry cleanup blocked, will retry later: {e}")
            continue
        cleanup_job_ids.append(job.id)

    return cleanup_job_ids


def _snapshot_already_absent(database, repository: str, snapshot_label: str) -> bool:
    """Whether `snapshot_label` is already gone from `repository` - a `schedule_cleanup` retry
    after a partial earlier attempt must treat this as already dropped (design.md "Drop
    snapshots outside metadata transactions").
    """
    try:
        prune.verify_snapshot_exists(database, repository, snapshot_label)
        return False
    except Exception:
        return True


def run_schedule_cleanup(
    cluster: Cluster, params: dict[str, Any], job_id: int, on_progress: OnProgress = None
) -> dict:
    """The `schedule_cleanup` job: drop every distinct snapshot a schedule's backup jobs
    reference, then delete the schedule's backup jobs (and their histories, references, and
    dependent restore jobs via FK cascade) and finally the schedule itself.

    Per design.md "Drop snapshots outside metadata transactions": references are read into a
    short-lived session that is closed before any StarRocks call, and metadata deletion happens
    in a separate short transaction only after every distinct snapshot has been dropped (or was
    already absent). A schedule with no references (e.g. only FAILED backup jobs) skips snapshot
    dropping and goes straight to metadata cleanup.
    """
    del on_progress  # no per-snapshot progress log for this job type, same as prune

    schedule_id = params["schedule_id"]

    with session_scope() as session:
        references = schedule_cleanup_dal.distinct_references_for_schedule(session, schedule_id)

    if references:
        database = connect(cluster)
        with database:
            ensure_ready(database, cluster)
            for repository, snapshot_label in references:
                if _snapshot_already_absent(database, repository, snapshot_label):
                    continue
                prune.execute_drop_snapshot(database, repository, snapshot_label)

    with session_scope() as session:
        schedules_dal.clear_last_run_job_id(session, schedule_id)
        schedule_cleanup_dal.delete_schedule_backup_jobs(session, schedule_id)
        schedule = session.get(Schedule, schedule_id)
        if schedule is not None:
            session.delete(schedule)

    return {"schedule_id": schedule_id, "snapshots_dropped": len(references)}
