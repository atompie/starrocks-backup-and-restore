from datetime import datetime

from sqlalchemy.orm import Session

from . import logger
from .dal.db import prune as prune_dal
from .dal.metadata import prune as prune_metadata_dal


def get_successful_backups(session: Session, cluster_id: int, group: int) -> list[dict]:
    """Get all successful backups belonging to an inventory group's backup catalog.

    Per specs/api-job-execution "Prune requests specify exactly one pruning strategy",
    prune is always scoped to one inventory group - there is no cluster-level default
    repository to filter by any more, so each returned backup carries its own
    recorded `repository` instead (see openspec/changes/decouple-database-and-
    repository-from-cluster/design.md "`group_id` becomes required for prune").

    Args:
        session: SQLite metastore session
        cluster_id: Cluster this backup belongs to
        group: Inventory group id to filter by

    Returns:
        List of backup records as dicts with keys: label, finished_at, repository, inventory_group_id
    """
    return prune_metadata_dal.get_successful_backups(session, cluster_id, group)


def filter_snapshots_to_delete(all_snapshots: list[dict], strategy: str, **kwargs) -> list[dict]:
    """Filter snapshots based on pruning strategy.

    Args:
        all_snapshots: List of snapshot dicts (must be sorted by finished_at ASC)
        strategy: Pruning strategy - 'keep_last', 'older_than', 'specific', or 'multiple'
        **kwargs: Strategy-specific parameters:
            - keep_last: 'count' (int) - number of backups to keep
            - older_than: 'timestamp' (str) - timestamp in 'YYYY-MM-DD HH:MM:SS' format
            - specific: 'snapshot' (str) - specific snapshot name
            - multiple: 'snapshots' (list) - list of snapshot names

    Returns:
        List of snapshots to delete
    """
    if strategy == "keep_last":
        count = kwargs.get("count")
        if count is None or count <= 0:
            raise ValueError("keep_last strategy requires a positive count")

        # Keep the last N, delete the rest
        if len(all_snapshots) <= count:
            return []
        return all_snapshots[:-count]  # Delete all except last N

    elif strategy == "older_than":
        timestamp_str = kwargs.get("timestamp")
        if not timestamp_str:
            raise ValueError("older_than strategy requires a timestamp")

        try:
            cutoff = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        except ValueError as e:
            raise ValueError(
                f"Invalid timestamp format '{timestamp_str}'. Expected 'YYYY-MM-DD HH:MM:SS'"
            ) from e

        to_delete = []
        for snapshot in all_snapshots:
            snapshot_time = datetime.strptime(snapshot["finished_at"], "%Y-%m-%d %H:%M:%S")
            if snapshot_time < cutoff:
                to_delete.append(snapshot)

        return to_delete

    elif strategy == "specific":
        snapshot_name = kwargs.get("snapshot")
        if not snapshot_name:
            raise ValueError("specific strategy requires a snapshot name")

        for snapshot in all_snapshots:
            if snapshot["label"] == snapshot_name:
                return [snapshot]

        return []

    elif strategy == "multiple":
        snapshot_names = kwargs.get("snapshots")
        if not snapshot_names:
            raise ValueError("multiple strategy requires a list of snapshot names")

        to_delete = []
        for snapshot in all_snapshots:
            if snapshot["label"] in snapshot_names:
                to_delete.append(snapshot)

        return to_delete

    else:
        raise ValueError(f"Unknown pruning strategy: {strategy}")


def verify_snapshot_exists(db, repository: str, snapshot_name: str) -> bool:
    """Verify that a snapshot exists in the repository.

    Args:
        db: Database connection
        repository: Repository name
        snapshot_name: Snapshot name to verify

    Returns:
        True if snapshot exists, False otherwise

    Raises:
        Exception if snapshot is not found
    """
    return prune_dal.verify_snapshot_exists(db, repository, snapshot_name)


def execute_drop_snapshot(db, repository: str, snapshot_name: str) -> None:
    """Execute DROP SNAPSHOT command for a single snapshot.

    Args:
        db: Database connection
        repository: Repository name
        snapshot_name: Snapshot name to delete

    Raises:
        Exception if deletion fails
    """
    prune_dal.execute_drop_snapshot(db, repository, snapshot_name)


def cleanup_backup_history(session: Session, cluster_id: int, snapshot_label: str) -> None:
    """Remove a pruned snapshot's catalog entry and partition manifest.

    Deletes the `Job` row for this backup label rather than a `backup_history` row - that
    table is now an immutable execution log (see add-job-history-log's design.md "Pruning a
    snapshot deletes its `Job` row instead of a `backup_history` row"). Deleting the `Job`
    row cascades to its `backup_history` rows via the existing `ON DELETE CASCADE`, removing
    the pruned backup's full execution log along with its catalog entry.

    Args:
        session: SQLite metastore session
        cluster_id: Cluster this backup belongs to
        snapshot_label: Snapshot label to remove from the catalog
    """
    try:
        prune_metadata_dal.cleanup_backup_history(session, cluster_id, snapshot_label)
        logger.debug(f"Cleaned up backup history for: {snapshot_label}")
    except Exception as e:
        logger.warning(f"Failed to cleanup backup history for '{snapshot_label}': {e}")
