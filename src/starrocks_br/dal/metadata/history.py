import json

from sqlalchemy.orm import Session, sessionmaker

from ...store.models import BackupHistory, RestoreHistory, RetentionHistory

# Terminal rows are always appended, bypassing the unchanged-state dedup
# check - StarRocks itself never reports these two values, so they can never
# collide with a StarRocks-native state already recorded for the job.
# `SNAPSHOT_DROPPED` is a retention event, one per dropped backup, never a repeated state.
TERMINAL_STATUSES = {"SUCCESS", "FAILED", "SNAPSHOT_DROPPED"}


def _append_event(
    model: type[BackupHistory] | type[RestoreHistory] | type[RetentionHistory],
    session_factory: sessionmaker[Session],
    job_id: int,
    status: str,
    message: str | None,
    details: dict | None,
) -> None:
    with session_factory() as session:
        if status not in TERMINAL_STATUSES:
            last = (
                session.query(model)
                .filter_by(job_id=job_id)
                .order_by(model.id.desc())
                .first()
            )
            if last is not None and last.status == status:
                return

        session.add(
            model(
                job_id=job_id,
                status=status,
                message=message,
                details_json=json.dumps(details) if details is not None else None,
            )
        )
        session.commit()


def append_backup_event(
    session_factory: sessionmaker[Session],
    job_id: int,
    status: str,
    message: str | None = None,
    details: dict | None = None,
) -> None:
    """Append a state-change row to a backup job's history, if it isn't a duplicate.

    Opens its own short-lived session (see AGENTS.md's parallel-execution
    rules). A no-op when `status` matches the job's most recently recorded
    row, except for a terminal `SUCCESS`/`FAILED` row, which is always
    appended.
    """
    _append_event(BackupHistory, session_factory, job_id, status, message, details)


def append_restore_event(
    session_factory: sessionmaker[Session],
    job_id: int,
    status: str,
    message: str | None = None,
    details: dict | None = None,
) -> None:
    """Append a state-change row to a restore job's history, if it isn't a duplicate.

    Mirrors `append_backup_event` for `restore_history`.
    """
    _append_event(RestoreHistory, session_factory, job_id, status, message, details)


def append_retention_event(
    session_factory: sessionmaker[Session],
    job_id: int,
    status: str,
    message: str | None = None,
    details: dict | None = None,
) -> None:
    """Append an event to a retention job's history.

    Mirrors `append_backup_event` for `retention_history`. `SNAPSHOT_DROPPED` is always appended,
    as each one records a different backup.
    """
    _append_event(RetentionHistory, session_factory, job_id, status, message, details)
