from ... import utils


def submit(db, command: str) -> None:
    """Execute a BACKUP command against StarRocks."""
    db.execute(command)


def show_backup(db, database: str) -> list:
    """Run SHOW BACKUP FROM <database> and return the raw rows."""
    return db.query(f"SHOW BACKUP FROM {utils.quote_identifier(database)}")


def show_tables(db, database: str) -> list:
    """Run SHOW TABLES FROM <database> and return the raw rows."""
    return db.query(f"SHOW TABLES FROM {utils.quote_identifier(database)}")


def show_partitions(db, database: str, table: str) -> list:
    """Run SHOW PARTITIONS FROM <database>.<table> and return the raw rows."""
    return db.query(f"SHOW PARTITIONS FROM {utils.build_qualified_table_name(database, table)}")


def get_partitions_meta(db, database: str, table_names: list[str] | None) -> list:
    """Query information_schema.partitions_meta for a database, optionally filtered by table.

    `table_names` of None or an empty list means "every table in the database"
    (the inventory's '*' wildcard); otherwise only those tables are matched.
    """
    where_conditions = [f"DB_NAME = {utils.quote_value(database)}", "PARTITION_NAME IS NOT NULL"]

    if table_names:
        table_conditions = [f"TABLE_NAME = {utils.quote_value(name)}" for name in table_names]
        where_conditions.append("(" + " OR ".join(table_conditions) + ")")

    where_clause = " AND ".join(where_conditions)

    query = f"""
    SELECT DB_NAME, TABLE_NAME, PARTITION_NAME
    FROM information_schema.partitions_meta
    WHERE {where_clause}
    ORDER BY TABLE_NAME, PARTITION_NAME
    """

    return db.query(query)


def build_incremental_backup_command(
    partitions: list[dict[str, str]], repository: str, label: str, database: str
) -> str:
    """Build BACKUP command for incremental backup of specific partitions.

    Args:
        partitions: List of partitions to backup
        repository: Repository name
        label: Backup label
        database: Database name (StarRocks requires BACKUP to be database-specific)

    Note: Filters partitions to only include those from the specified database.
    """
    if not partitions:
        return ""

    db_partitions = [p for p in partitions if p["database"] == database]

    if not db_partitions:
        return ""

    table_partitions = {}
    for partition in db_partitions:
        table_name = partition["table"]
        if table_name not in table_partitions:
            table_partitions[table_name] = []
        table_partitions[table_name].append(partition["partition_name"])

    on_clauses = []
    for table, parts in table_partitions.items():
        partitions_str = ", ".join(utils.quote_identifier(p) for p in parts)
        on_clauses.append(f"TABLE {utils.quote_identifier(table)} PARTITION ({partitions_str})")

    on_clause = ",\n    ".join(on_clauses)

    command = f"""BACKUP DATABASE {utils.quote_identifier(database)} SNAPSHOT {utils.quote_identifier(label)}
    TO {utils.quote_identifier(repository)}
    ON ({on_clause})"""

    return command


def build_full_backup_command(
    db_entries: list[dict[str, str]], repository: str, label: str, database: str
) -> str:
    """Build BACKUP command for an inventory group.

    If the group contains '*' for any entry in the target database, generate a
    simple BACKUP DATABASE command. Otherwise, generate ON (TABLE ...) list for
    the specific tables within the database.

    Args:
        db_entries: This group's table memberships already filtered to `database`
            (empty entries mean "nothing to back up in this database" - the
            caller returns "" without calling this function).
    """
    if any(t["table"] == "*" for t in db_entries):
        return f"""BACKUP DATABASE {utils.quote_identifier(database)} SNAPSHOT {utils.quote_identifier(label)}
    TO {utils.quote_identifier(repository)}"""

    on_clauses = []
    for t in db_entries:
        on_clauses.append(f"TABLE {utils.quote_identifier(t['table'])}")
    on_clause = ",\n        ".join(on_clauses)
    return f"""BACKUP DATABASE {utils.quote_identifier(database)} SNAPSHOT {utils.quote_identifier(label)}
    TO {utils.quote_identifier(repository)}
    ON ({on_clause})"""
