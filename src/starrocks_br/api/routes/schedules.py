"""Schedule CRUD and the run-due trigger.

Per design.md Decision 5: run_due() advances each due schedule's
next_run_at with a conditional UPDATE (WHERE next_run_at <= now, matching
the value already read) *before* submitting its job, in the same
transaction. A second concurrent call only sees a row it can no longer
conditionally advance and skips it - this is what makes run-due idempotent
per occurrence without a separate lock table.
"""

import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ... import exceptions
from ...commands import schedules as schedules_commands
from ...dal.metadata import inventory_groups
from ...jobs.backend import UnknownBackendError
from ...store.models import Schedule
from ..auth import require_api_key
from ..deps import get_db
from ..schemas import RunDueResponse, ScheduleCreate, ScheduleRead, ScheduleUpdate
from ._cluster_connect import ensure_repository_exists, get_cluster_or_404

router = APIRouter(tags=["schedules"], dependencies=[Depends(require_api_key)])


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _get_schedule_or_404(db: Session, cluster_id: int, schedule_id: int) -> Schedule:
    schedule = schedules_commands.get_schedule(db, cluster_id, schedule_id)
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    return schedule


@router.post(
    "/backup/schedules/cluster/{cluster_id}",
    response_model=ScheduleRead,
    status_code=status.HTTP_201_CREATED,
)
def create_schedule(cluster_id: int, payload: ScheduleCreate, db: Session = Depends(get_db)) -> Schedule:
    cluster = get_cluster_or_404(db, cluster_id)

    if not inventory_groups.group_exists(db, cluster_id, payload.inventory_group_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory group id {payload.inventory_group_id} not found on this cluster",
        )

    ensure_repository_exists(cluster, payload.repository)

    try:
        return schedules_commands.create_schedule(
            db,
            cluster,
            job_type=payload.job_type,
            inventory_group_id=payload.inventory_group_id,
            repository=payload.repository,
            cadence=payload.cadence,
            backend=payload.backend,
            enabled=payload.enabled,
            retention=payload.retention,
            expire_after_days=payload.expire_after_days,
        )
    except (exceptions.InvalidCadenceError, exceptions.InvalidScheduleFieldsError) as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e
    except UnknownBackendError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e


@router.get("/backup/schedules/cluster/{cluster_id}", response_model=list[ScheduleRead])
def list_schedules(cluster_id: int, db: Session = Depends(get_db)) -> list[Schedule]:
    get_cluster_or_404(db, cluster_id)
    return schedules_commands.list_schedules(db, cluster_id)


@router.get(
    "/backup/schedules/cluster/{cluster_id}/schedule_id/{schedule_id}", response_model=ScheduleRead
)
def get_schedule(cluster_id: int, schedule_id: int, db: Session = Depends(get_db)) -> Schedule:
    get_cluster_or_404(db, cluster_id)
    return _get_schedule_or_404(db, cluster_id, schedule_id)


@router.patch(
    "/backup/schedules/cluster/{cluster_id}/schedule_id/{schedule_id}", response_model=ScheduleRead
)
def update_schedule(
    cluster_id: int, schedule_id: int, payload: ScheduleUpdate, db: Session = Depends(get_db)
) -> Schedule:
    cluster = get_cluster_or_404(db, cluster_id)
    schedule = _get_schedule_or_404(db, cluster_id, schedule_id)

    if schedule.cadence is None:
        # A one-shot schedule is immutable - reject before running any other
        # validation (an unknown group/repository in the same request should
        # not produce a 404/503 instead of this 409).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exceptions.ScheduleImmutableError(schedule.id)),
        )

    updates = payload.model_dump(exclude_unset=True)
    if "inventory_group_id" in updates and not inventory_groups.group_exists(
        db, cluster_id, updates["inventory_group_id"]
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory group id {updates['inventory_group_id']} not found on this cluster",
        )
    if "repository" in updates:
        ensure_repository_exists(cluster, updates["repository"])

    try:
        return schedules_commands.update_schedule(db, schedule, updates)
    except exceptions.ScheduleImmutableError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except (exceptions.InvalidCadenceError, exceptions.InvalidScheduleFieldsError) as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e


@router.delete(
    "/backup/schedules/cluster/{cluster_id}/schedule_id/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_schedule(cluster_id: int, schedule_id: int, db: Session = Depends(get_db)) -> None:
    get_cluster_or_404(db, cluster_id)
    schedule = _get_schedule_or_404(db, cluster_id, schedule_id)
    schedules_commands.delete_schedule(db, schedule)


@router.post("/backup/schedules/run", response_model=RunDueResponse)
def run_due(db: Session = Depends(get_db)) -> RunDueResponse:
    try:
        triggered_job_ids, triggered_count = schedules_commands.run_due_schedules(db, _utcnow())
    except exceptions.InvalidCadenceError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e
    except UnknownBackendError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e
    return RunDueResponse(triggered_job_ids=triggered_job_ids, triggered_count=triggered_count)
