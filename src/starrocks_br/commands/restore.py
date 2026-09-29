"""The single implementation of the restore use case.

See `commands/backup.py` for the module-level rationale.
"""

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from .. import restore
from ..dal.metadata import restore_catalog
from ..exceptions import NoTablesFoundError, RestoreExecutionError, RestoreSourcePendingDeletionError
from ..store.models import Cluster, Job
from ..store.session import session_scope
from ._shared import connect, ensure_ready
from .jobs import submit_job

OnProgress = Callable[[dict], None] | None


def submit_restore_job(
    db: Session, cluster: Cluster, params: dict, requested_backend: str | None
) -> Job:
    """Resolve the restore's source backup and record it as `source_backup_job_id`, rejecting
    submission if that backup's schedule is pending deletion (design.md "Use a durable
    restore-to-backup relationship" / specs/api-job-execution "Restore submission cannot race
    with source backup cleanup"). A `target_label` with no resolvable successful backup Job is
    left with no source link - submission still proceeds and fails during execution exactly as
    before, since resolving the label is not itself new validation.
    """
    target_label = params["target_label"]
    source_job = restore_catalog.find_successful_job(db, cluster.id, target_label)
    source_backup_job_id = source_job.id if source_job is not None else None

    if source_backup_job_id is not None and restore_catalog.source_schedule_pending_deletion(
        db, source_backup_job_id
    ):
        raise RestoreSourcePendingDeletionError(target_label)

    return submit_job(
        db, cluster, "restore", params, requested_backend, source_backup_job_id=source_backup_job_id
    )


def run_restore(
    cluster: Cluster,
    params: dict[str, Any],
    job_id: int,
    on_progress: OnProgress = None,
    *,
    skip_confirmation: bool = True,
) -> dict:
    target_label = params["target_label"]
    group = params.get("group_id")
    table = params.get("table")
    table_database = params.get("database")
    rename_suffix = params.get("rename_suffix") or "_restored"

    if group and table:
        raise ValueError("Cannot specify both 'group_id' and 'table'")
    if table and not table_database:
        raise ValueError("'database' is required when 'table' is specified")

    database = connect(cluster)
    with database:
        with session_scope() as session:
            repository = restore.find_backup_repository(session, cluster.id, target_label)

        ensure_ready(database, cluster, repository=repository)

        with session_scope() as session:
            restore_pair = restore.find_restore_pair(session, cluster.id, target_label)

            tables_to_restore = restore.get_tables_from_backup(
                database,
                session,
                cluster.id,
                target_label,
                group=group,
                table=table,
                database=table_database if table else None,
            )
        if not tables_to_restore:
            raise NoTablesFoundError(group=group, label=target_label)

        with session_scope() as session:
            result = restore.execute_restore_flow(
                database,
                session,
                cluster.id,
                repository,
                restore_pair,
                tables_to_restore,
                rename_suffix,
                skip_confirmation=skip_confirmation,
                job_id=job_id,
                on_progress=on_progress,
            )

        if not result["success"]:
            raise RestoreExecutionError(result["error_message"])

        return {"restore_pair": restore_pair, "tables": tables_to_restore, "message": result.get("message")}
