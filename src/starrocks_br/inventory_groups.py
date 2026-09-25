"""Inventory group CRUD, scoped to a registered cluster's `table_inventory`.

Mirrors `repository.py`: SQL-building + `StarRocksDB` calls, no HTTP
knowledge. `table_inventory` is a StarRocks UNIQUE KEY table, so a naive
`INSERT` on an existing key would silently replace the row rather than
error - membership conflicts are therefore detected with an existence
check before inserting, not by catching an insert error.
"""

from __future__ import annotations

from . import utils


class InventoryGroupNotFoundError(RuntimeError):
    """Raised when an inventory group has no rows in table_inventory."""


class InventoryMembershipConflictError(RuntimeError):
    """Raised when inserting a (group, database, table) row that already exists."""


class InventoryMembershipNotFoundError(RuntimeError):
    """Raised when removing a (group, database, table) row that does not exist."""


def _field(row, dict_key: str, tuple_index: int):
    if isinstance(row, dict):
        return row.get(dict_key)
    return row[tuple_index]


def list_groups(db, ops_database: str = "ops") -> list[dict]:
    """List every inventory group with its table-membership count."""
    rows = db.query(
        f"SELECT inventory_group, COUNT(*) FROM {ops_database}.table_inventory "
        f"GROUP BY inventory_group ORDER BY inventory_group"
    )
    return [
        {"name": _field(row, "inventory_group", 0), "table_count": _field(row, "count", 1)}
        for row in rows
    ]


def group_exists(db, group_name: str, ops_database: str = "ops") -> bool:
    """Return whether `group_name` has at least one row in table_inventory."""
    rows = db.query(
        f"SELECT 1 FROM {ops_database}.table_inventory "
        f"WHERE inventory_group = {utils.quote_value(group_name)} LIMIT 1"
    )
    return len(rows) > 0


def get_group(db, group_name: str, ops_database: str = "ops") -> list[dict]:
    """Return every membership for `group_name`, or `[]` if it has none.

    The caller decides whether an empty result means "unknown group" (404).
    """
    rows = db.query(
        f"SELECT database_name, table_name, created_at, updated_at "
        f"FROM {ops_database}.table_inventory "
        f"WHERE inventory_group = {utils.quote_value(group_name)} "
        f"ORDER BY database_name, table_name"
    )
    return [
        {
            "database": _field(row, "database_name", 0),
            "table": _field(row, "table_name", 1),
            "created_at": str(_field(row, "created_at", 2)),
            "updated_at": str(_field(row, "updated_at", 3)),
        }
        for row in rows
    ]


def _membership_exists(db, group_name: str, database_name: str, table_name: str, ops_database: str) -> bool:
    rows = db.query(
        f"SELECT 1 FROM {ops_database}.table_inventory "
        f"WHERE inventory_group = {utils.quote_value(group_name)} "
        f"AND database_name = {utils.quote_value(database_name)} "
        f"AND table_name = {utils.quote_value(table_name)} LIMIT 1"
    )
    return len(rows) > 0


def add_membership(
    db, group_name: str, database_name: str, table_name: str, ops_database: str = "ops"
) -> dict:
    """Insert a single (group, database, table) membership row.

    Raises:
        InventoryMembershipConflictError: If that exact membership already exists.
    """
    if _membership_exists(db, group_name, database_name, table_name, ops_database):
        raise InventoryMembershipConflictError(
            f"Membership ({group_name}, {database_name}, {table_name}) already exists"
        )

    db.execute(
        f"INSERT INTO {ops_database}.table_inventory "
        f"(inventory_group, database_name, table_name) VALUES "
        f"({utils.quote_value(group_name)}, {utils.quote_value(database_name)}, {utils.quote_value(table_name)})"
    )
    return {"group": group_name, "database": database_name, "table": table_name}


def add_memberships_bulk(
    db, group_name: str, entries: list[tuple[str, str]], ops_database: str = "ops"
) -> list[dict]:
    """Add several (database, table) memberships to `group_name`.

    Matches `schema.bootstrap_table_inventory`'s existing idempotency:
    entries that already exist are silently skipped rather than raising, so
    partial success across the loop is acceptable and a retry is safe.
    """
    added = []
    for database_name, table_name in entries:
        try:
            added.append(add_membership(db, group_name, database_name, table_name, ops_database))
        except InventoryMembershipConflictError:
            continue
    return added


def remove_membership(
    db, group_name: str, database_name: str, table_name: str, ops_database: str = "ops"
) -> None:
    """Remove a single (group, database, table) membership row.

    Raises:
        InventoryMembershipNotFoundError: If that membership does not exist.
    """
    if not _membership_exists(db, group_name, database_name, table_name, ops_database):
        raise InventoryMembershipNotFoundError(
            f"Membership ({group_name}, {database_name}, {table_name}) not found"
        )

    db.execute(
        f"DELETE FROM {ops_database}.table_inventory "
        f"WHERE inventory_group = {utils.quote_value(group_name)} "
        f"AND database_name = {utils.quote_value(database_name)} "
        f"AND table_name = {utils.quote_value(table_name)}"
    )


def delete_group(db, group_name: str, ops_database: str = "ops") -> int:
    """Delete every membership row for `group_name`.

    Raises:
        InventoryGroupNotFoundError: If the group has zero rows.

    Returns:
        The number of rows deleted.
    """
    rows = db.query(
        f"SELECT COUNT(*) FROM {ops_database}.table_inventory "
        f"WHERE inventory_group = {utils.quote_value(group_name)}"
    )
    count = _field(rows[0], "count", 0) if rows else 0
    if not count:
        raise InventoryGroupNotFoundError(f"Inventory group '{group_name}' not found")

    db.execute(
        f"DELETE FROM {ops_database}.table_inventory "
        f"WHERE inventory_group = {utils.quote_value(group_name)}"
    )
    return count
