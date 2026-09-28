import datetime

from sqlalchemy.orm import Session

from starrocks_br import exceptions, logger, timezone
from starrocks_br.dal.db import backup as backup_dal
from starrocks_br.dal.metadata import backup_catalog


def find_latest_full_backup(db, session: Session, cluster_id: int, database: str) -> dict[str, str] | None:
    """Find the latest successful full backup for a database.

    Resolved from `Job` (`label`/`job_type`/`status`/`finished_at`) rather than a separate
    backup catalog - see add-job-history-log's design.md "The backup catalog moves from
    `backup_history` to two new columns on `Job`".

    Args:
        db: Database connection (only used for its `.timezone`)
        session: SQLite metastore session
        cluster_id: Cluster this backup belongs to
        database: Database name to search for

    Returns:
        Dictionary with keys: label, backup_type, finished_at, job_id, or None if no full backup
        found. The finished_at value is returned as a string in the cluster timezone format.
    """
    job = backup_catalog.find_latest_full_backup_job(session, cluster_id, database)

    if job is None:
        return None

    finished_at = job.finished_at

    if isinstance(finished_at, datetime.datetime):
        finished_at_normalized = timezone.normalize_datetime_to_tz(finished_at, db.timezone)
        finished_at = finished_at_normalized.strftime("%Y-%m-%d %H:%M:%S")
    elif not isinstance(finished_at, str):
        finished_at = str(finished_at)

    return {"label": job.label, "backup_type": "full", "finished_at": finished_at, "job_id": job.id}


def find_tables_by_group(session: Session, cluster_id: int, group_id: int) -> list[dict[str, str]]:
    """Find tables belonging to a specific inventory group.

    Returns list of dictionaries with keys: database, table.
    Supports '*' table wildcard which signifies all tables in a database.
    """
    rows = backup_catalog.list_group_table_memberships(session, cluster_id, group_id)
    return [{"database": row.database_name, "table": row.table_name} for row in rows]


def resolve_group_databases(session: Session, cluster_id: int, group_id: int) -> list[str]:
    """Resolve the distinct databases a group's table memberships belong to.

    Backup command-building (`build_full_backup_command`, `build_incremental_backup_command`,
    etc.) is written around one database per operation - there is no cluster-level default
    database to fall back to any more (see openspec/changes/decouple-database-and-repository-
    from-cluster/design.md "Group database derivation"). An inventory group may span more than
    one database (SPEC.md §5, PLAN.md §0.2); the caller runs one backup operation per database
    returned here, all under the same Backup Job (see backup-references/design.md Decision 4).

    Raises:
        NoTablesFoundError: If the group has no table memberships.
    """
    tables = find_tables_by_group(session, cluster_id, group_id)
    if not tables:
        raise exceptions.NoTablesFoundError(group=group_id)

    return sorted({t["database"] for t in tables})


def validate_tables_exist(
    db, database: str, tables: list[dict[str, str]], group: int | None = None
) -> None:
    """Validate that tables in the inventory actually exist in the database.

    `tables` may span more than one database when the group is multi-database (SPEC.md §5); this
    only validates the subset belonging to `database`, since the caller runs one such check per
    database in the group (see `resolve_group_databases`).

    Args:
        db: Database connection
        database: Database name to validate tables against
        tables: List of tables with keys: database, table
        group: Optional inventory group id for better error messages

    Raises:
        InvalidTablesInInventoryError: If any tables don't exist in the database
    """
    if not tables:
        return

    db_tables = [t for t in tables if t["database"] == database and t["table"] != "*"]

    if not db_tables:
        return

    existing_tables_rows = backup_dal.show_tables(db, database)
    existing_tables = {row[0] for row in existing_tables_rows}

    invalid_tables = []
    for table_entry in db_tables:
        table_name = table_entry["table"]
        if table_name not in existing_tables:
            invalid_tables.append(table_name)

    if invalid_tables:
        raise exceptions.InvalidTablesInInventoryError(database, invalid_tables, group)


def find_recent_partitions(
    db,
    session: Session,
    cluster_id: int,
    database: str,
    baseline_backup_label: str | None = None,
    *,
    group_id: int,
) -> tuple[list[dict[str, str]], int | None]:
    """Find partitions updated since baseline for tables in the given inventory group.

    Args:
        db: Database connection
        session: SQLite metastore session
        cluster_id: Cluster this backup history/table inventory belongs to
        database: Database name (StarRocks database scope for backup)
        baseline_backup_label: Optional specific backup label to use as baseline.
        group_id: Id of the inventory group whose tables will be considered

    Returns a tuple of (partitions, baseline_job_id): `partitions` is a list of dictionaries with
    keys database/table/partition_name, scoped to the specified database; `baseline_job_id` is the
    id of the full backup `Job` used as the baseline, for the caller to record as an incremental
    job's `Job.baseline_job_id`.
    """
    cluster_tz = db.timezone

    if baseline_backup_label:
        baseline_job = backup_catalog.find_successful_job_by_label(session, cluster_id, baseline_backup_label)
        if baseline_job is None:
            raise exceptions.BackupLabelNotFoundError(baseline_backup_label)
        baseline_time_raw = baseline_job.finished_at
        baseline_job_id = baseline_job.id
    else:
        latest_backup = find_latest_full_backup(db, session, cluster_id, database)
        if not latest_backup:
            raise exceptions.NoFullBackupFoundError(database)
        baseline_time_raw = latest_backup["finished_at"]
        baseline_job_id = latest_backup["job_id"]

    if isinstance(baseline_time_raw, datetime.datetime):
        baseline_time_str = baseline_time_raw.strftime("%Y-%m-%d %H:%M:%S")
    elif isinstance(baseline_time_raw, str):
        baseline_time_str = baseline_time_raw
    else:
        baseline_time_str = str(baseline_time_raw)

    baseline_dt = timezone.parse_datetime_with_tz(baseline_time_str, cluster_tz)

    group_tables = find_tables_by_group(session, cluster_id, group_id)

    if not group_tables:
        return [], baseline_job_id

    db_group_tables = [t for t in group_tables if t["database"] == database]

    if not db_group_tables:
        return [], baseline_job_id

    concrete_tables = []
    for table_entry in db_group_tables:
        if table_entry["table"] == "*":
            tables_rows = backup_dal.show_tables(db, table_entry["database"])
            for row in tables_rows:
                concrete_tables.append({"database": table_entry["database"], "table": row[0]})
        else:
            concrete_tables.append(table_entry)

    recent_partitions = []
    for table_entry in concrete_tables:
        db_name = table_entry["database"]
        table_name = table_entry["table"]

        try:
            partition_rows = backup_dal.show_partitions(db, db_name, table_name)
        except Exception as e:
            logger.error(f"Error showing partitions for table {db_name}.{table_name}: {e}")
            continue

        for row in partition_rows:
            # FOR SHARED NOTHING CLUSTER:
            # PartitionId, PartitionName, VisibleVersion, VisibleVersionTime, VisibleVersionHash, State, PartitionKey, Range, DistributionKey, Buckets, ReplicationNum, StorageMedium, CooldownTime, LastConsistencyCheckTime, DataSize, StorageSize, IsInMemory, RowCount, DataVersion, VersionEpoch, VersionTxnType
            partition_name = row[1]
            visible_version_time = row[3]

            if isinstance(visible_version_time, datetime.datetime):
                visible_version_time_str = visible_version_time.strftime("%Y-%m-%d %H:%M:%S")
            elif isinstance(visible_version_time, str):
                visible_version_time_str = visible_version_time
            else:
                visible_version_time_str = str(visible_version_time)

            visible_version_dt = timezone.parse_datetime_with_tz(
                visible_version_time_str, cluster_tz
            )

            if visible_version_dt > baseline_dt:
                recent_partitions.append(
                    {"database": db_name, "table": table_name, "partition_name": partition_name}
                )

    return recent_partitions, baseline_job_id


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

    return backup_dal.build_incremental_backup_command(db_partitions, repository, label, database)


def build_full_backup_command(
    session: Session, cluster_id: int, group_id: int, repository: str, label: str, database: str
) -> str:
    """Build BACKUP command for an inventory group.

    If the group contains '*' for any entry in the target database, generate a
    simple BACKUP DATABASE command. Otherwise, generate ON (TABLE ...) list for
    the specific tables within the database.
    """
    tables = find_tables_by_group(session, cluster_id, group_id)

    db_entries = [t for t in tables if t["database"] == database]
    if not db_entries:
        return ""

    return backup_dal.build_full_backup_command(db_entries, repository, label, database)


def record_backup_references(
    session: Session,
    job_id: int,
    repository: str,
    snapshot_label: str,
    snapshot_timestamp: datetime.datetime,
    partitions: list[dict[str, str]],
) -> None:
    """Record reference metadata for a finished backup in the backup_references table.

    Called only once the job's StarRocks operation has reached `FINISHED` - a failed job records
    no references (SPEC.md §16).

    Args:
        session: SQLite metastore session
        job_id: The backup Job these references belong to
        repository: Repository name the snapshot was written to
        snapshot_label: StarRocks snapshot/backup label
        snapshot_timestamp: When the backup finished
        partitions: List of partitions with keys: database, table, partition_name
    """
    backup_catalog.record_references(session, job_id, repository, snapshot_label, snapshot_timestamp, partitions)


def get_all_partitions_for_tables(
    db, database: str, tables: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Get all existing partitions for the specified tables.

    Args:
        db: Database connection
        database: Database name
        tables: List of tables with keys: database, table

    Returns:
        List of partitions with keys: database, table, partition_name
    """
    if not tables:
        return []

    db_tables = [t for t in tables if t["database"] == database]
    if not db_tables:
        return []

    table_names = [table["table"] for table in db_tables if table["table"] != "*"]

    rows = backup_dal.get_partitions_meta(db, database, table_names if table_names else None)

    return [{"database": row[0], "table": row[1], "partition_name": row[2]} for row in rows]
