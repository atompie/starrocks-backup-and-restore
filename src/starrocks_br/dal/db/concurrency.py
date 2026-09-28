from ... import logger, utils


def is_backup_job_stale(db, label: str) -> bool:
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
