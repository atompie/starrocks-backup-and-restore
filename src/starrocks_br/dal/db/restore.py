import datetime

from ... import exceptions, utils


def get_snapshot_timestamp(db, repo_name: str, snapshot_name: str) -> str:
    """Get the backup timestamp for a specific snapshot from the repository.

    Args:
        db: Database connection
        repo_name: Repository name
        snapshot_name: Snapshot name to look up

    Returns:
        The backup timestamp string

    Raises:
        ValueError: If snapshot is not found in the repository
    """
    query = f"SHOW SNAPSHOT ON {utils.quote_identifier(repo_name)} WHERE Snapshot = {utils.quote_value(snapshot_name)}"

    rows = db.query(query)
    if not rows:
        raise exceptions.SnapshotNotFoundError(snapshot_name, repo_name)

    # The result should be a single row with columns: Snapshot, Timestamp, Status
    result = rows[0]

    if isinstance(result, dict):
        timestamp = result.get("Timestamp")
    else:
        timestamp = result[1] if len(result) > 1 else None

    if not timestamp:
        raise ValueError(f"Could not extract timestamp for snapshot '{snapshot_name}'")

    return timestamp


def show_restore(db, database: str) -> list:
    """Run SHOW RESTORE FROM <database> and return the raw rows."""
    return db.query(f"SHOW RESTORE FROM {utils.quote_identifier(database)}")


def submit(db, command: str) -> None:
    """Execute a RESTORE command against StarRocks."""
    db.execute(command)


def show_tables(db, database: str) -> list:
    """Run SHOW TABLES FROM <database> and return the raw rows."""
    return db.query(f"SHOW TABLES FROM {utils.quote_identifier(database)}")


def _generate_timestamped_backup_name(table_name: str) -> str:
    """Generate a timestamped backup table name.

    Args:
        table_name: Original table name

    Returns:
        Timestamped backup name in format: {table_name}_backup_YYYYMMDD_HHMMSS
    """
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{table_name}_backup_{timestamp}"


def perform_atomic_rename(db, tables: list[str], rename_suffix: str) -> dict:
    """Perform atomic rename of temporary tables to make them live."""
    try:
        rename_statements = []
        existing_by_database: dict[str, set[str]] = {}
        for table in tables:
            database, table_name = table.split(".", 1)
            temp_table_name = f"{table_name}{rename_suffix}"

            if database not in existing_by_database:
                existing_by_database[database] = {
                    row[0] for row in db.query(f"SHOW TABLES FROM {utils.quote_identifier(database)}")
                }

            # The live table is absent when restoring into a dropped database/table;
            # there is nothing to move aside in that case.
            if table_name in existing_by_database[database]:
                backup_table_name = _generate_timestamped_backup_name(table_name)
                rename_statements.append(
                    f"ALTER TABLE {utils.build_qualified_table_name(database, table_name)} RENAME {utils.quote_identifier(backup_table_name)}"
                )
            rename_statements.append(
                f"ALTER TABLE {utils.build_qualified_table_name(database, temp_table_name)} RENAME {utils.quote_identifier(table_name)}"
            )

        for statement in rename_statements:
            db.execute(statement)

        return {"success": True}

    except Exception as e:
        return {"success": False, "error_message": f"Failed to perform atomic rename: {str(e)}"}


def build_partition_restore_command(
    database: str,
    table: str,
    partition: str,
    backup_label: str,
    repository: str,
    backup_timestamp: str,
) -> str:
    """Build RESTORE command for single partition recovery."""
    return f"""RESTORE SNAPSHOT {utils.quote_identifier(backup_label)}
    FROM {utils.quote_identifier(repository)}
    DATABASE {utils.quote_identifier(database)}
    ON (TABLE {utils.quote_identifier(table)} PARTITION ({utils.quote_identifier(partition)}))
    PROPERTIES ("backup_timestamp" = "{backup_timestamp}")"""


def build_table_restore_command(
    database: str,
    table: str,
    backup_label: str,
    repository: str,
    backup_timestamp: str,
) -> str:
    """Build RESTORE command for full table recovery."""
    return f"""RESTORE SNAPSHOT {utils.quote_identifier(backup_label)}
    FROM {utils.quote_identifier(repository)}
    DATABASE {utils.quote_identifier(database)}
    ON (TABLE {utils.quote_identifier(table)})
    PROPERTIES ("backup_timestamp" = "{backup_timestamp}")"""


def build_database_restore_command(
    database: str,
    backup_label: str,
    repository: str,
    backup_timestamp: str,
) -> str:
    """Build RESTORE command for full database recovery."""
    return f"""RESTORE SNAPSHOT {utils.quote_identifier(backup_label)}
    FROM {utils.quote_identifier(repository)}
    DATABASE {utils.quote_identifier(database)}
    PROPERTIES ("backup_timestamp" = "{backup_timestamp}")"""


def build_restore_command_with_rename(
    backup_label: str,
    repo_name: str,
    tables: list[str],
    rename_suffix: str,
    database: str,
    backup_timestamp: str,
) -> str:
    """Build restore command with AS clause for temporary table names."""
    table_clauses = []
    for table in tables:
        _, table_name = table.split(".", 1)
        temp_table_name = f"{table_name}{rename_suffix}"
        table_clauses.append(
            f"TABLE {utils.quote_identifier(table_name)} AS {utils.quote_identifier(temp_table_name)}"
        )

    on_clause = ",\n    ".join(table_clauses)

    return f"""RESTORE SNAPSHOT {utils.quote_identifier(backup_label)}
    FROM {utils.quote_identifier(repo_name)}
    DATABASE {utils.quote_identifier(database)}
    ON ({on_clause})
    PROPERTIES ("backup_timestamp" = "{backup_timestamp}")"""


def build_restore_command_without_rename(
    backup_label: str, repo_name: str, tables: list[str], database: str, backup_timestamp: str
) -> str:
    """Build restore command without AS clause (for incremental restores to existing temp tables)."""
    table_clauses = []
    for table in tables:
        _, table_name = table.split(".", 1)
        table_clauses.append(f"TABLE {utils.quote_identifier(table_name)}")

    on_clause = ",\n    ".join(table_clauses)

    return f"""RESTORE SNAPSHOT {utils.quote_identifier(backup_label)}
    FROM {utils.quote_identifier(repo_name)}
    DATABASE {utils.quote_identifier(database)}
    ON ({on_clause})
    PROPERTIES ("backup_timestamp" = "{backup_timestamp}")"""


def build_partition_restore_command_for_incremental(
    backup_label: str,
    repo_name: str,
    table: str,
    partitions: list[str],
    database: str,
    backup_timestamp: str,
    rename_suffix: str | None = None,
) -> str:
    """Build partition-level restore command with optional AS clause.

    Args:
        backup_label: Backup snapshot label
        repo_name: Repository name
        table: Table name in format 'database.table'
        partitions: List of partition names to restore
        database: Database name
        backup_timestamp: Backup timestamp
        rename_suffix: Optional suffix for AS clause (e.g., '_restored')

    Returns:
        SQL RESTORE command string
    """
    _, table_name = table.split(".", 1)

    # Build partition list
    partition_list = ", ".join([utils.quote_identifier(p) for p in partitions])

    # Build table clause
    if rename_suffix:
        # Table only in incremental: use AS clause
        temp_table_name = f"{table_name}{rename_suffix}"
        table_clause = f"TABLE {utils.quote_identifier(table_name)} PARTITION ({partition_list}) AS {utils.quote_identifier(temp_table_name)}"
    else:
        # Table in base: target the _restored table directly (no AS)
        table_clause = f"TABLE {utils.quote_identifier(table_name)} PARTITION ({partition_list})"

    return f"""RESTORE SNAPSHOT {utils.quote_identifier(backup_label)}
    FROM {utils.quote_identifier(repo_name)}
    DATABASE {utils.quote_identifier(database)}
    ON ({table_clause})
    PROPERTIES ("backup_timestamp" = "{backup_timestamp}")"""
