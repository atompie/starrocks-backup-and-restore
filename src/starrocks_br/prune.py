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

from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from . import logger
from .store.models import BackupHistory, BackupPartition, TableInventory


def get_successful_backups(session: Session, cluster_id: int, group: int) -> list[dict]:
    """Get all successful backups belonging to an inventory group's backup history.

    Per specs/api-job-execution "Prune requests specify exactly one pruning strategy",
    prune is always scoped to one inventory group - there is no cluster-level default
    repository to filter by any more, so each returned backup carries its own
    recorded `repository` instead (see openspec/changes/decouple-database-and-
    repository-from-cluster/design.md "`group_id` becomes required for prune").

    Args:
        session: SQLite metastore session
        cluster_id: Cluster this backup history belongs to
        group: Inventory group id to filter by

    Returns:
        List of backup records as dicts with keys: label, finished_at, repository, inventory_group_id
    """
    rows = session.execute(
        select(
            BackupHistory.label,
            BackupHistory.finished_at,
            BackupHistory.repository,
            TableInventory.inventory_group_id,
        )
        .distinct()
        .join(BackupPartition, BackupPartition.label == BackupHistory.label)
        .join(
            TableInventory,
            and_(
                TableInventory.database_name == BackupPartition.database_name,
                or_(TableInventory.table_name == BackupPartition.table_name, TableInventory.table_name == "*"),
                TableInventory.cluster_id == cluster_id,
            ),
        )
        .where(
            BackupHistory.cluster_id == cluster_id,
            BackupPartition.cluster_id == cluster_id,
            BackupHistory.status == "FINISHED",
            TableInventory.inventory_group_id == group,
        )
        .order_by(BackupHistory.finished_at.asc())
    ).all()

    return [
        {
            "label": label,
            "finished_at": str(finished_at),
            "repository": repository,
            "inventory_group_id": inventory_group_id,
        }
        for label, finished_at, repository, inventory_group_id in rows
    ]


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
    sql = f"SHOW SNAPSHOT ON {repository} WHERE SNAPSHOT = '{snapshot_name}'"

    try:
        rows = db.query(sql)
        if not rows:
            raise Exception(f"Snapshot '{snapshot_name}' not found in repository '{repository}'")
        return True
    except Exception as e:
        logger.error(f"Failed to verify snapshot '{snapshot_name}': {e}")
        raise


def execute_drop_snapshot(db, repository: str, snapshot_name: str) -> None:
    """Execute DROP SNAPSHOT command for a single snapshot.

    Args:
        db: Database connection
        repository: Repository name
        snapshot_name: Snapshot name to delete

    Raises:
        Exception if deletion fails
    """
    sql = f"DROP SNAPSHOT ON {repository} WHERE SNAPSHOT = '{snapshot_name}'"

    try:
        logger.info(f"Deleting snapshot: {snapshot_name}")
        db.execute(sql)
        logger.success(f"Successfully deleted snapshot: {snapshot_name}")
    except Exception as e:
        logger.error(f"Failed to delete snapshot '{snapshot_name}': {e}")
        raise


def cleanup_backup_history(session: Session, cluster_id: int, snapshot_label: str) -> None:
    """Remove backup history entry after snapshot deletion.

    Args:
        session: SQLite metastore session
        cluster_id: Cluster this backup history belongs to
        snapshot_label: Snapshot label to remove from history
    """
    try:
        session.execute(
            BackupPartition.__table__.delete().where(
                BackupPartition.cluster_id == cluster_id, BackupPartition.label == snapshot_label
            )
        )
        session.execute(
            BackupHistory.__table__.delete().where(
                BackupHistory.cluster_id == cluster_id, BackupHistory.label == snapshot_label
            )
        )
        session.flush()
        logger.debug(f"Cleaned up backup history for: {snapshot_label}")
    except Exception as e:
        logger.warning(f"Failed to cleanup backup history for '{snapshot_label}': {e}")
