"""StarRocks-side lookup used to reconcile jobs whose owning process died.

Answers one question - "what does StarRocks say about this backup/restore operation?" - and
nothing else; deciding what to do with the answer is `commands/jobs.py::reconcile_stale_jobs`.
"""

from typing import Literal

from . import logger
from .dal.db import backup as backup_dal
from .dal.db import restore as restore_dal

# StarRocks' terminal states for both SHOW BACKUP and SHOW RESTORE; anything else is in progress.
TERMINAL_STATES = {"FINISHED", "CANCELLED"}


def _row_label_and_state(kind: Literal["backup", "restore"], row) -> tuple[str, str]:
    if kind == "backup":
        if isinstance(row, dict):
            return row.get("SnapshotName", ""), row.get("State", "UNKNOWN")
        return (row[1] if len(row) > 1 else ""), (row[3] if len(row) > 3 else "UNKNOWN")
    if isinstance(row, dict):
        return row.get("Label", ""), row.get("State", "UNKNOWN")
    return (row[1] if len(row) > 1 else ""), (row[4] if len(row) > 4 else "UNKNOWN")


def find_operation_state(
    db, kind: Literal["backup", "restore"], databases: list[str], labels: list[str]
) -> str | None:
    """State StarRocks reports for the operation named by any of `labels`, or None if not found.

    `SHOW BACKUP`/`SHOW RESTORE` only return the most recent operation of a database, so an
    operation that has since been overwritten by a newer one is reported as not found - the
    same "lost" case `poll_backup_status` returns. A database that cannot be queried (dropped,
    permissions) is skipped rather than failing the lookup; a connection failure is not
    caught here and propagates so the caller can treat the cluster as unreachable.
    """
    show = backup_dal.show_backup if kind == "backup" else restore_dal.show_restore
    wanted = set(labels)

    for database in databases:
        try:
            rows = show(db, database)
        except Exception as e:
            logger.warning(f"Could not run SHOW {kind.upper()} for database '{database}': {e}")
            continue
        if not rows:
            continue
        label, state = _row_label_and_state(kind, rows[0])
        if label in wanted:
            return state
    return None
