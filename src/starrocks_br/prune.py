"""Snapshot helpers shared by schedule cleanup: check for and drop a StarRocks snapshot."""

from .dal.db import prune as prune_dal


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

