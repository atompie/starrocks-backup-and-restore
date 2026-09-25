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

import datetime
import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from starrocks_br import exceptions, logger, timezone, utils
from starrocks_br.store.models import BackupHistory, BackupPartition, TableInventory


def find_latest_full_backup(db, session: Session, cluster_id: int, database: str) -> dict[str, str] | None:
    """Find the latest successful full backup for a database.

    Args:
        db: Database connection (only used for its `.timezone`)
        session: SQLite metastore session
        cluster_id: Cluster this backup history belongs to
        database: Database name to search for

    Returns:
        Dictionary with keys: label, backup_type, finished_at, or None if no full backup found.
        The finished_at value is returned as a string in the cluster timezone format.
    """
    row = session.scalars(
        select(BackupHistory)
        .where(
            BackupHistory.cluster_id == cluster_id,
            BackupHistory.backup_type == "full",
            BackupHistory.status == "FINISHED",
            BackupHistory.label.like(f"{database}_%"),
        )
        .order_by(BackupHistory.finished_at.desc())
        .limit(1)
    ).first()

    if row is None:
        return None

    finished_at = row.finished_at

    if isinstance(finished_at, datetime.datetime):
        finished_at_normalized = timezone.normalize_datetime_to_tz(finished_at, db.timezone)
        finished_at = finished_at_normalized.strftime("%Y-%m-%d %H:%M:%S")
    elif not isinstance(finished_at, str):
        finished_at = str(finished_at)

    return {"label": row.label, "backup_type": row.backup_type, "finished_at": finished_at}


def find_tables_by_group(session: Session, cluster_id: int, group_name: str) -> list[dict[str, str]]:
    """Find tables belonging to a specific inventory group.

    Returns list of dictionaries with keys: database, table.
    Supports '*' table wildcard which signifies all tables in a database.
    """
    rows = session.scalars(
        select(TableInventory)
        .where(TableInventory.cluster_id == cluster_id, TableInventory.inventory_group == group_name)
        .order_by(TableInventory.database_name, TableInventory.table_name)
    )
    return [{"database": row.database_name, "table": row.table_name} for row in rows]


def validate_tables_exist(
    db, database: str, tables: list[dict[str, str]], group: str = None
) -> None:
    """Validate that tables in the inventory actually exist in the database.

    Args:
        db: Database connection
        database: Database name to validate tables against
        tables: List of tables with keys: database, table
        group: Optional inventory group name for better error messages

    Raises:
        InvalidTablesInInventoryError: If any tables don't exist in the database
    """
    if not tables:
        return

    db_tables = [t for t in tables if t["database"] == database and t["table"] != "*"]

    if not db_tables:
        return

    show_tables_query = f"SHOW TABLES FROM {utils.quote_identifier(database)}"
    existing_tables_rows = db.query(show_tables_query)
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
    group_name: str,
) -> list[dict[str, str]]:
    """Find partitions updated since baseline for tables in the given inventory group.

    Args:
        db: Database connection
        session: SQLite metastore session
        cluster_id: Cluster this backup history/table inventory belongs to
        database: Database name (StarRocks database scope for backup)
        baseline_backup_label: Optional specific backup label to use as baseline.
        group_name: Inventory group whose tables will be considered

    Returns list of dictionaries with keys: database, table, partition_name.
    Only partitions of tables within the specified database are returned.
    """
    cluster_tz = db.timezone

    if baseline_backup_label:
        baseline_row = session.scalars(
            select(BackupHistory).where(
                BackupHistory.cluster_id == cluster_id,
                BackupHistory.label == baseline_backup_label,
                BackupHistory.status == "FINISHED",
            )
        ).first()
        if baseline_row is None:
            raise exceptions.BackupLabelNotFoundError(baseline_backup_label)
        baseline_time_raw = baseline_row.finished_at
    else:
        latest_backup = find_latest_full_backup(db, session, cluster_id, database)
        if not latest_backup:
            raise exceptions.NoFullBackupFoundError(database)
        baseline_time_raw = latest_backup["finished_at"]

    if isinstance(baseline_time_raw, datetime.datetime):
        baseline_time_str = baseline_time_raw.strftime("%Y-%m-%d %H:%M:%S")
    elif isinstance(baseline_time_raw, str):
        baseline_time_str = baseline_time_raw
    else:
        baseline_time_str = str(baseline_time_raw)

    baseline_dt = timezone.parse_datetime_with_tz(baseline_time_str, cluster_tz)

    group_tables = find_tables_by_group(session, cluster_id, group_name)

    if not group_tables:
        return []

    db_group_tables = [t for t in group_tables if t["database"] == database]

    if not db_group_tables:
        return []

    concrete_tables = []
    for table_entry in db_group_tables:
        if table_entry["table"] == "*":
            show_tables_query = (
                f"SHOW TABLES FROM {utils.quote_identifier(table_entry['database'])}"
            )
            tables_rows = db.query(show_tables_query)
            for row in tables_rows:
                concrete_tables.append({"database": table_entry["database"], "table": row[0]})
        else:
            concrete_tables.append(table_entry)

    recent_partitions = []
    for table_entry in concrete_tables:
        db_name = table_entry["database"]
        table_name = table_entry["table"]

        show_partitions_query = (
            f"SHOW PARTITIONS FROM {utils.build_qualified_table_name(db_name, table_name)}"
        )
        try:
            partition_rows = db.query(show_partitions_query)
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

    return recent_partitions


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
    session: Session, cluster_id: int, group_name: str, repository: str, label: str, database: str
) -> str:
    """Build BACKUP command for an inventory group.

    If the group contains '*' for any entry in the target database, generate a
    simple BACKUP DATABASE command. Otherwise, generate ON (TABLE ...) list for
    the specific tables within the database.
    """
    tables = find_tables_by_group(session, cluster_id, group_name)

    db_entries = [t for t in tables if t["database"] == database]
    if not db_entries:
        return ""

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


def record_backup_partitions(
    session: Session, cluster_id: int, label: str, partitions: list[dict[str, str]]
) -> None:
    """Record partition metadata for a backup in the backup_partitions table.

    Args:
        session: SQLite metastore session
        cluster_id: Cluster this backup belongs to
        label: Backup label
        partitions: List of partitions with keys: database, table, partition_name
    """
    if not partitions:
        return

    for partition in partitions:
        composite_key = (
            f"{label}|{partition['database']}|{partition['table']}|{partition['partition_name']}"
        )
        key_hash = hashlib.md5(composite_key.encode("utf-8")).hexdigest()

        session.add(
            BackupPartition(
                cluster_id=cluster_id,
                key_hash=key_hash,
                label=label,
                database_name=partition["database"],
                table_name=partition["table"],
                partition_name=partition["partition_name"],
            )
        )
    session.flush()


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

    where_conditions = [f"DB_NAME = {utils.quote_value(database)}", "PARTITION_NAME IS NOT NULL"]

    table_conditions = []
    for table in db_tables:
        if table["table"] == "*":
            pass
        else:
            table_conditions.append(f"TABLE_NAME = {utils.quote_value(table['table'])}")

    if table_conditions:
        where_conditions.append("(" + " OR ".join(table_conditions) + ")")

    where_clause = " AND ".join(where_conditions)

    query = f"""
    SELECT DB_NAME, TABLE_NAME, PARTITION_NAME
    FROM information_schema.partitions_meta 
    WHERE {where_clause}
    ORDER BY TABLE_NAME, PARTITION_NAME
    """

    rows = db.query(query)

    return [{"database": row[0], "table": row[1], "partition_name": row[2]} for row in rows]
