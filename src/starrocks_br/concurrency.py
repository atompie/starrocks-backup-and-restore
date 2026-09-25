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

import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import exceptions, logger, utils
from .store.models import RunStatus


def reserve_job_slot(db, session: Session, cluster_id: int, scope: str, label: str) -> None:
    """Reserve a job slot in the run_status table to prevent overlapping jobs.

    We consider any row with state='ACTIVE' for the same scope as a conflict.
    However, we implement self-healing logic to automatically clean up stale locks.
    """
    active_jobs = _get_active_jobs_for_scope(session, cluster_id, scope)

    if not active_jobs:
        _insert_new_job(session, cluster_id, scope, label)
        return

    _handle_active_job_conflicts(db, session, cluster_id, scope, active_jobs)

    _insert_new_job(session, cluster_id, scope, label)


def _get_active_jobs_for_scope(session: Session, cluster_id: int, scope: str) -> list[tuple[str, str, str]]:
    """Get all active jobs for the given scope."""
    rows = session.execute(
        select(RunStatus.scope, RunStatus.label, RunStatus.state).where(
            RunStatus.cluster_id == cluster_id, RunStatus.state == "ACTIVE"
        )
    ).all()
    return [tuple(row) for row in rows if row[0] == scope]


def _handle_active_job_conflicts(
    db, session: Session, cluster_id: int, scope: str, active_jobs: list[tuple[str, str, str]]
) -> None:
    """Handle conflicts with active jobs, cleaning up stale ones where possible."""
    for active_scope, active_label, _ in active_jobs:
        if _can_heal_stale_job(db, active_scope, active_label):
            _cleanup_stale_job(session, cluster_id, active_scope, active_label)
            logger.success(f"Cleaned up stale backup job: {active_label}")
        else:
            _raise_concurrency_conflict(scope, active_jobs)


def _can_heal_stale_job(db, scope: str, label: str) -> bool:
    """Check if a stale job can be healed (only for backup jobs)."""
    if scope != "backup":
        return False

    return _is_backup_job_stale(db, label)


def _raise_concurrency_conflict(scope: str, active_jobs: list[tuple[str, str, str]]) -> None:
    """Raise a concurrency conflict error with helpful message."""
    raise exceptions.ConcurrencyConflictError(scope, active_jobs)


def _insert_new_job(session: Session, cluster_id: int, scope: str, label: str) -> None:
    """Insert a new active job record."""
    session.add(RunStatus(cluster_id=cluster_id, scope=scope, label=label, state="ACTIVE"))
    session.flush()


def _is_backup_job_stale(db, label: str) -> bool:
    """Check if a backup job is stale by querying StarRocks SHOW BACKUP.

    Returns True if the job is stale (not actually running), False if it's still active.
    """
    try:
        user_databases = _get_user_databases(db)

        for database_name in user_databases:
            job_status = _check_backup_job_in_database(db, database_name, label)

            if job_status is None:
                continue

            if job_status == "active":
                return False
            elif job_status == "stale":
                return True

        return True

    except Exception as e:
        logger.error(f"Error checking backup job status: {e}")
        return False


def _get_user_databases(db) -> list[str]:
    """Get list of user databases (excluding system databases)."""
    system_databases = {"information_schema", "mysql", "sys"}

    databases = db.query("SHOW DATABASES")
    return [
        _extract_database_name(db_row)
        for db_row in databases
        if _extract_database_name(db_row) not in system_databases
    ]


def _extract_database_name(db_row) -> str:
    """Extract database name from database query result."""
    if isinstance(db_row, (list, tuple)):
        return db_row[0]
    return db_row.get("Database", "")


def _check_backup_job_in_database(db, database_name: str, label: str) -> str:
    """Check if backup job exists in specific database and return its status.

    Returns:
        'active' if job is still running
        'stale' if job is in terminal state
        None if job not found in this database
    """
    try:
        show_backup_query = f"SHOW BACKUP FROM {utils.quote_identifier(database_name)}"
        backup_rows = db.query(show_backup_query)

        if not backup_rows:
            return None

        result = backup_rows[0]
        snapshot_name, state = _extract_backup_info(result)

        if snapshot_name != label:
            return None

        if state in ["FINISHED", "CANCELLED", "FAILED"]:
            return "stale"
        else:
            return "active"

    except Exception:
        return None


def _extract_backup_info(result) -> tuple[str, str]:
    """Extract snapshot name and state from SHOW BACKUP result."""
    if isinstance(result, dict):
        snapshot_name = result.get("SnapshotName", "")
        state = result.get("State", "UNKNOWN")
    else:
        snapshot_name = result[1] if len(result) > 1 else ""
        state = result[3] if len(result) > 3 else "UNKNOWN"

    return snapshot_name, state


def _cleanup_stale_job(session: Session, cluster_id: int, scope: str, label: str) -> None:
    """Clean up a stale job by updating its state to CANCELLED."""
    row = session.scalars(
        select(RunStatus).where(
            RunStatus.cluster_id == cluster_id,
            RunStatus.scope == scope,
            RunStatus.label == label,
            RunStatus.state == "ACTIVE",
        )
    ).one_or_none()
    if row is None:
        return
    row.state = "CANCELLED"
    row.finished_at = datetime.datetime.now(datetime.timezone.utc)
    session.flush()


def complete_job_slot(
    session: Session,
    cluster_id: int,
    scope: str,
    label: str,
    final_state: Literal["FINISHED", "FAILED", "CANCELLED"],
) -> None:
    """Complete job slot and persist final state.

    Simple approach: update the same row by scope/label.
    """
    row = session.scalars(
        select(RunStatus).where(
            RunStatus.cluster_id == cluster_id, RunStatus.scope == scope, RunStatus.label == label
        )
    ).one_or_none()
    if row is None:
        return
    row.state = final_state
    row.finished_at = datetime.datetime.now(datetime.timezone.utc)
    session.flush()
