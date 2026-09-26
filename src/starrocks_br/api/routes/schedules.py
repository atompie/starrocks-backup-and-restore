# Copyright 2025 deep-bi
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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

from ... import exceptions, inventory_groups
from ...commands.schedules import compute_next_run_at, run_due_schedules
from ...jobs.backend import UnknownBackendError
from ...store.models import Schedule
from ..auth import require_api_key
from ..deps import get_db
from ..schemas import RunDueResponse, ScheduleCreate, ScheduleRead, ScheduleUpdate
from ._cluster_connect import get_cluster_or_404

router = APIRouter(tags=["schedules"], dependencies=[Depends(require_api_key)])


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _compute_next_run_at(cadence: str, after: datetime.datetime | None = None) -> datetime.datetime:
    try:
        return compute_next_run_at(cadence, after)
    except exceptions.InvalidCadenceError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e


def _get_schedule_or_404(db: Session, cluster_id: int, schedule_id: int) -> Schedule:
    schedule = db.get(Schedule, schedule_id)
    if schedule is None or schedule.cluster_id != cluster_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    return schedule


@router.post(
    "/backup/schedules/cluster/{cluster_id}",
    response_model=ScheduleRead,
    status_code=status.HTTP_201_CREATED,
)
def create_schedule(cluster_id: int, payload: ScheduleCreate, db: Session = Depends(get_db)) -> Schedule:
    get_cluster_or_404(db, cluster_id)

    if not inventory_groups.group_exists(db, cluster_id, payload.inventory_group_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory group id {payload.inventory_group_id} not found on this cluster",
        )

    next_run_at = _compute_next_run_at(payload.cadence)

    schedule = Schedule(
        cluster_id=cluster_id,
        job_type=payload.job_type,
        inventory_group_id=payload.inventory_group_id,
        cadence=payload.cadence,
        backend=payload.backend,
        enabled=payload.enabled,
        next_run_at=next_run_at,
    )
    db.add(schedule)
    db.flush()
    db.refresh(schedule)
    return schedule


@router.get("/backup/schedules/cluster/{cluster_id}", response_model=list[ScheduleRead])
def list_schedules(cluster_id: int, db: Session = Depends(get_db)) -> list[Schedule]:
    get_cluster_or_404(db, cluster_id)
    return list(
        db.query(Schedule).filter(Schedule.cluster_id == cluster_id).order_by(Schedule.id).all()
    )


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
    get_cluster_or_404(db, cluster_id)
    schedule = _get_schedule_or_404(db, cluster_id, schedule_id)

    updates = payload.model_dump(exclude_unset=True)
    if "inventory_group_id" in updates and not inventory_groups.group_exists(
        db, cluster_id, updates["inventory_group_id"]
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory group id {updates['inventory_group_id']} not found on this cluster",
        )
    cadence_changed = "cadence" in updates
    for field, value in updates.items():
        setattr(schedule, field, value)

    if cadence_changed:
        schedule.next_run_at = _compute_next_run_at(schedule.cadence)

    db.flush()
    db.refresh(schedule)
    return schedule


@router.delete(
    "/backup/schedules/cluster/{cluster_id}/schedule_id/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_schedule(cluster_id: int, schedule_id: int, db: Session = Depends(get_db)) -> None:
    get_cluster_or_404(db, cluster_id)
    schedule = _get_schedule_or_404(db, cluster_id, schedule_id)
    db.delete(schedule)


@router.post("/backup/schedules/run", response_model=RunDueResponse)
def run_due(db: Session = Depends(get_db)) -> RunDueResponse:
    try:
        triggered_job_ids, triggered_count = run_due_schedules(db, _utcnow())
    except exceptions.InvalidCadenceError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e
    except UnknownBackendError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e
    return RunDueResponse(triggered_job_ids=triggered_job_ids, triggered_count=triggered_count)
