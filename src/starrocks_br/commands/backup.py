"""The single implementation of the backup_full/backup_incremental use cases.

Runs exactly the same sequence of core-library calls that `cli.py`'s
`backup full`/`backup incremental` adapters invoke (see
openspec/changes/establish-command-layer). Takes no dependency on HTTP or
CLI frameworks; `cli.py` and the API's job backend both call these
functions directly.
"""

from collections.abc import Callable
from typing import Any

from .. import concurrency, executor, labels, planner
from ..exceptions import BackupExecutionError, SnapshotAlreadyExistsError
from ..store.models import Cluster
from ..store.session import session_scope
from ._shared import connect, ensure_ready

OnProgress = Callable[[dict], None] | None


def _raise_for_backup_failure(result: dict) -> None:
    """Translate `executor.execute_backup`'s failure dict into a domain exception.

    `execute_backup` itself keeps returning `{"success": False, ...}` (see
    design.md Decision 2) - this is the one place that dict is translated,
    so both `cli.py` and the API's job backend see the same exception types.
    """
    error_details = result.get("error_details") or {}
    if error_details.get("error_type") == "snapshot_exists":
        raise SnapshotAlreadyExistsError(error_details["snapshot_name"])
    raise BackupExecutionError(result["error_message"], final_status=result.get("final_status"))


def run_backup_full(cluster: Cluster, params: dict[str, Any], on_progress: OnProgress = None) -> dict:
    group = params.get("group")
    if not group:
        raise ValueError("'group' is required for backup_full")
    name = params.get("name")

    database = connect(cluster)
    with database:
        ensure_ready(database, cluster)

        with session_scope() as session:
            label = labels.determine_backup_label(
                session, cluster.id, "full", cluster.database, custom_name=name
            )

            tables = planner.find_tables_by_group(session, cluster.id, group)
            planner.validate_tables_exist(database, cluster.database, tables, group)

            backup_command = planner.build_full_backup_command(
                session, cluster.id, group, cluster.repository, label, cluster.database
            )
            if not backup_command:
                raise RuntimeError(
                    f"No tables found in group '{group}' for database '{cluster.database}' to backup"
                )

            all_partitions = planner.get_all_partitions_for_tables(database, cluster.database, tables)

            concurrency.reserve_job_slot(database, session, cluster.id, "backup", label)
            planner.record_backup_partitions(session, cluster.id, label, all_partitions)

        with session_scope() as session:
            result = executor.execute_backup(
                database,
                session,
                cluster.id,
                backup_command,
                repository=cluster.repository,
                backup_type="full",
                scope="backup",
                database=cluster.database,
                on_progress=on_progress,
            )

        if not result["success"]:
            _raise_for_backup_failure(result)

        return {"label": label, "final_status": result["final_status"]}


def run_backup_incremental(
    cluster: Cluster, params: dict[str, Any], on_progress: OnProgress = None
) -> dict:
    group = params.get("group")
    if not group:
        raise ValueError("'group' is required for backup_incremental")
    name = params.get("name")
    baseline_backup = params.get("baseline_backup")

    database = connect(cluster)
    with database:
        ensure_ready(database, cluster)

        with session_scope() as session:
            label = labels.determine_backup_label(
                session, cluster.id, "incremental", cluster.database, custom_name=name
            )

            if baseline_backup:
                if on_progress:
                    on_progress({"event": "baseline_specified", "baseline_backup": baseline_backup})
            else:
                latest_backup = planner.find_latest_full_backup(
                    database, session, cluster.id, cluster.database
                )
                if on_progress:
                    on_progress({"event": "baseline_resolved", "latest_backup": latest_backup})

            partitions = planner.find_recent_partitions(
                database,
                session,
                cluster.id,
                cluster.database,
                baseline_backup_label=baseline_backup,
                group_name=group,
            )
            if not partitions:
                raise RuntimeError("No partitions found to backup")

            backup_command = planner.build_incremental_backup_command(
                partitions, cluster.repository, label, cluster.database
            )

            concurrency.reserve_job_slot(database, session, cluster.id, "backup", label)
            planner.record_backup_partitions(session, cluster.id, label, partitions)

        with session_scope() as session:
            result = executor.execute_backup(
                database,
                session,
                cluster.id,
                backup_command,
                repository=cluster.repository,
                backup_type="incremental",
                scope="backup",
                database=cluster.database,
                on_progress=on_progress,
            )

        if not result["success"]:
            _raise_for_backup_failure(result)

        return {"label": label, "final_status": result["final_status"]}
