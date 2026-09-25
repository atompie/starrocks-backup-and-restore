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

from croniter import CroniterBadCronError, croniter
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from ...store.models import Cluster, Schedule
from ..auth import require_api_key
from ..deps import get_db
from ..schemas import RunDueResponse, ScheduleCreate, ScheduleRead, ScheduleUpdate
from .jobs import submit_job

router = APIRouter(prefix="/schedules", tags=["schedules"], dependencies=[Depends(require_api_key)])


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _compute_next_run_at(cadence: str, after: datetime.datetime | None = None) -> datetime.datetime:
    base = after or _utcnow()
    try:
        itr = croniter(cadence, base)
        return itr.get_next(datetime.datetime)
    except (CroniterBadCronError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid cadence expression '{cadence}': {e}",
        ) from e


def _get_schedule_or_404(db: Session, schedule_id: int) -> Schedule:
    schedule = db.get(Schedule, schedule_id)
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    return schedule


@router.post("", response_model=ScheduleRead, status_code=status.HTTP_201_CREATED)
def create_schedule(payload: ScheduleCreate, db: Session = Depends(get_db)) -> Schedule:
    cluster = db.get(Cluster, payload.cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cluster not found")

    next_run_at = _compute_next_run_at(payload.cadence)

    schedule = Schedule(
        cluster_id=payload.cluster_id,
        job_type=payload.job_type,
        group_name=payload.group_name,
        cadence=payload.cadence,
        backend=payload.backend,
        enabled=payload.enabled,
        next_run_at=next_run_at,
    )
    db.add(schedule)
    db.flush()
    db.refresh(schedule)
    return schedule


@router.get("", response_model=list[ScheduleRead])
def list_schedules(db: Session = Depends(get_db)) -> list[Schedule]:
    return list(db.query(Schedule).order_by(Schedule.id).all())


@router.get("/{schedule_id}", response_model=ScheduleRead)
def get_schedule(schedule_id: int, db: Session = Depends(get_db)) -> Schedule:
    return _get_schedule_or_404(db, schedule_id)


@router.patch("/{schedule_id}", response_model=ScheduleRead)
def update_schedule(
    schedule_id: int, payload: ScheduleUpdate, db: Session = Depends(get_db)
) -> Schedule:
    schedule = _get_schedule_or_404(db, schedule_id)

    updates = payload.model_dump(exclude_unset=True)
    cadence_changed = "cadence" in updates
    for field, value in updates.items():
        setattr(schedule, field, value)

    if cadence_changed:
        schedule.next_run_at = _compute_next_run_at(schedule.cadence)

    db.flush()
    db.refresh(schedule)
    return schedule


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(schedule_id: int, db: Session = Depends(get_db)) -> None:
    schedule = _get_schedule_or_404(db, schedule_id)
    db.delete(schedule)


@router.post("/run-due", response_model=RunDueResponse)
def run_due(db: Session = Depends(get_db)) -> RunDueResponse:
    now = _utcnow()
    due_schedules = (
        db.query(Schedule)
        .filter(Schedule.enabled.is_(True), Schedule.next_run_at <= now)
        .all()
    )

    triggered_job_ids: list[int] = []

    for schedule in due_schedules:
        previously_due_at = schedule.next_run_at
        new_next_run_at = _compute_next_run_at(schedule.cadence, after=now)

        result = db.execute(
            update(Schedule)
            .where(Schedule.id == schedule.id, Schedule.next_run_at == previously_due_at)
            .values(next_run_at=new_next_run_at)
        )
        if result.rowcount == 0:
            # Another concurrent run-due call already advanced this schedule
            # past this due occurrence - skip to stay idempotent.
            continue

        cluster = db.get(Cluster, schedule.cluster_id)
        job = submit_job(
            db,
            cluster,
            schedule.job_type,
            {"group": schedule.group_name},
            schedule.backend,
        )
        schedule.last_run_job_id = job.id
        triggered_job_ids.append(job.id)

    db.flush()
    return RunDueResponse(triggered_job_ids=triggered_job_ids, triggered_count=len(triggered_job_ids))
