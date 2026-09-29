"""Unauthenticated liveness endpoint - deliberately has no `require_api_key` dependency."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...commands import schedules as schedules_commands
from ..deps import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    """Report liveness plus `scheduler.last_tick_at`, the last successful scheduler tick.

    `last_tick_at` is null until a tick has ever completed. It lets an operator notice a
    stalled cron/timer even though the API process does not run the scheduler itself.
    """
    last_tick_at = schedules_commands.get_scheduler_last_tick_at(db)
    return {
        "status": "ok",
        "scheduler": {"last_tick_at": last_tick_at.isoformat() if last_tick_at else None},
    }
