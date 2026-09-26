"""The single implementation of the restore use case.

See `commands/backup.py` for the module-level rationale.
"""

from collections.abc import Callable
from typing import Any

from .. import restore
from ..exceptions import NoTablesFoundError, RestoreExecutionError
from ..store.models import Cluster
from ..store.session import session_scope
from ._shared import connect, ensure_ready

OnProgress = Callable[[dict], None] | None


def run_restore(
    cluster: Cluster,
    params: dict[str, Any],
    on_progress: OnProgress = None,
    *,
    skip_confirmation: bool = True,
) -> dict:
    target_label = params["target_label"]
    group = params.get("group_id")
    table = params.get("table")
    rename_suffix = params.get("rename_suffix") or "_restored"

    if group and table:
        raise ValueError("Cannot specify both 'group_id' and 'table'")

    database = connect(cluster)
    with database:
        ensure_ready(database, cluster)

        with session_scope() as session:
            restore_pair = restore.find_restore_pair(session, cluster.id, target_label)

            tables_to_restore = restore.get_tables_from_backup(
                database,
                session,
                cluster.id,
                target_label,
                group=group,
                table=table,
                database=cluster.database if table else None,
            )
        if not tables_to_restore:
            raise NoTablesFoundError(group=group, label=target_label)

        with session_scope() as session:
            result = restore.execute_restore_flow(
                database,
                session,
                cluster.id,
                cluster.repository,
                restore_pair,
                tables_to_restore,
                rename_suffix,
                skip_confirmation=skip_confirmation,
                on_progress=on_progress,
            )

        if not result["success"]:
            raise RestoreExecutionError(result["error_message"])

        return {"restore_pair": restore_pair, "tables": tables_to_restore, "message": result.get("message")}
