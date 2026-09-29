"""Singleton `scheduler_lock` row: tick mutual exclusion and last-successful-tick timestamp.

Acquisition is one atomic conditional UPDATE, portable across SQLite/Postgres/MySQL. The caller
(`commands/schedules.py`) decides the lease length and what to log; this module only runs the
queries.
"""

import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...store.models import SchedulerLock

LOCK_ROW_ID = 1


def _ensure_row(session: Session) -> None:
    """Create the singleton row if a database was built without the migration's seed row."""
    if session.get(SchedulerLock, LOCK_ROW_ID) is not None:
        return
    try:
        with session.begin_nested():
            session.add(SchedulerLock(id=LOCK_ROW_ID))
    except IntegrityError:
        pass  # another process seeded it first


def try_acquire(
    session: Session, holder: str, now: datetime.datetime, expires_at: datetime.datetime
) -> tuple[bool, bool]:
    """Try to take the lock. Returns `(acquired, recovered_stale)`.

    `recovered_stale` is True only when the lock was taken over from a holder that never
    released it (its `expires_at` was still set, but in the past).
    """
    _ensure_row(session)
    previous_expires_at = session.scalar(
        select(SchedulerLock.expires_at).where(SchedulerLock.id == LOCK_ROW_ID)
    )

    result = session.execute(
        update(SchedulerLock)
        .where(
            SchedulerLock.id == LOCK_ROW_ID,
            or_(SchedulerLock.expires_at.is_(None), SchedulerLock.expires_at < now),
        )
        .values(holder=holder, acquired_at=now, expires_at=expires_at)
    )
    session.flush()
    acquired = result.rowcount == 1
    return acquired, acquired and previous_expires_at is not None


def release(session: Session, holder: str) -> bool:
    """Release the lock only if `holder` still owns it (a reclaimed lock is left alone)."""
    result = session.execute(
        update(SchedulerLock)
        .where(SchedulerLock.id == LOCK_ROW_ID, SchedulerLock.holder == holder)
        .values(holder=None, acquired_at=None, expires_at=None)
    )
    session.flush()
    return result.rowcount == 1


def record_last_tick(session: Session, now: datetime.datetime) -> None:
    _ensure_row(session)
    session.execute(
        update(SchedulerLock).where(SchedulerLock.id == LOCK_ROW_ID).values(last_tick_at=now)
    )
    session.flush()


def get_last_tick_at(session: Session) -> datetime.datetime | None:
    return session.scalar(select(SchedulerLock.last_tick_at).where(SchedulerLock.id == LOCK_ROW_ID))
