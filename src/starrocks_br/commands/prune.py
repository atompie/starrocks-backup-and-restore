"""The single implementation of the prune use case.

See `commands/backup.py` for the module-level rationale.
"""

from collections.abc import Callable
from typing import Any

from .. import prune
from ..store.models import Cluster
from ..store.session import session_scope
from ._shared import connect, ensure_ready

OnProgress = Callable[[dict], None] | None


def run_prune(cluster: Cluster, params: dict[str, Any], on_progress: OnProgress = None) -> dict:
    del on_progress  # prune has no long-running per-snapshot progress to report

    group = params.get("group_id")
    keep_last = params.get("keep_last")
    older_than = params.get("older_than")
    snapshot = params.get("snapshot")
    snapshots = params.get("snapshots")
    dry_run = bool(params.get("dry_run"))

    specified = [opt for opt in (keep_last, older_than, snapshot, snapshots) if opt is not None]
    if len(specified) != 1:
        raise ValueError(
            "Must specify exactly one of: keep_last, older_than, snapshot, snapshots"
        )

    database = connect(cluster)
    with database:
        ensure_ready(database, cluster)

        if keep_last:
            strategy, kwargs = "keep_last", {"count": keep_last}
        elif older_than:
            strategy, kwargs = "older_than", {"timestamp": older_than}
        elif snapshot:
            strategy, kwargs = "specific", {"snapshot": snapshot}
        else:
            snapshot_list = [s.strip() for s in snapshots.split(",")]
            strategy, kwargs = "multiple", {"snapshots": snapshot_list}

        # No long-running submit-and-poll operation here (DROP SNAPSHOT is a
        # single synchronous call per snapshot), so one session for the whole
        # command body is fine - unlike backup/restore, there's no multi-minute
        # StarRocks wait to avoid holding a write lock across.
        with session_scope() as session:
            all_backups = prune.get_successful_backups(session, cluster.id, cluster.repository, group=group)
            if not all_backups:
                return {"deleted": [], "kept_count": 0}

            if strategy in ("specific", "multiple"):
                for snap in kwargs.get("snapshots", [kwargs.get("snapshot")]):
                    prune.verify_snapshot_exists(database, cluster.repository, snap)

            snapshots_to_delete = prune.filter_snapshots_to_delete(all_backups, strategy, **kwargs)

            if dry_run or not snapshots_to_delete:
                return {
                    "deleted": [],
                    "would_delete": [s["label"] for s in snapshots_to_delete],
                    "kept_count": len(all_backups) - len(snapshots_to_delete),
                }

            deleted = []
            for snap in snapshots_to_delete:
                prune.execute_drop_snapshot(database, cluster.repository, snap["label"])
                prune.cleanup_backup_history(session, cluster.id, snap["label"])
                deleted.append(snap["label"])

            return {"deleted": deleted, "kept_count": len(all_backups) - len(deleted)}
