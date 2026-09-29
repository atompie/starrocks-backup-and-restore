from ... import logger, utils


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
    sql = f"SHOW SNAPSHOT ON {utils.quote_identifier(repository)} WHERE SNAPSHOT = {utils.quote_value(snapshot_name)}"

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
    sql = f"DROP SNAPSHOT ON {utils.quote_identifier(repository)} WHERE SNAPSHOT = {utils.quote_value(snapshot_name)}"

    try:
        logger.info(f"Deleting snapshot: {snapshot_name}")
        db.execute(sql)
        logger.success(f"Successfully deleted snapshot: {snapshot_name}")
    except Exception as e:
        logger.error(f"Failed to delete snapshot '{snapshot_name}': {e}")
        raise


def snapshot_present(db, repository: str, snapshot_name: str) -> bool:
    """Whether `snapshot_name` is listed in `repository`.

    Unlike `verify_snapshot_exists`, a failing query is not read as "absent": it propagates, so
    a transient StarRocks error can never be mistaken for an already-dropped snapshot.
    """
    sql = f"SHOW SNAPSHOT ON {utils.quote_identifier(repository)} WHERE SNAPSHOT = {utils.quote_value(snapshot_name)}"
    return bool(db.query(sql))
