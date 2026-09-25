"""Inventory group CRUD, scoped to a registered cluster's `table_inventory`.

`table_inventory` lives in the app's own SQLite metastore (see
`store.models.TableInventory`), scoped by `cluster_id` - not in a StarRocks
database. It has a `UniqueConstraint` on (cluster_id, inventory_group,
database_name, table_name), but membership conflicts are still detected with
an existence check before inserting, not by catching an IntegrityError - the
constraint is a backstop, matching the pre-existing StarRocks-era behavior
where a naive INSERT into a UNIQUE KEY table would silently replace the row
rather than error.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import logger
from .store.models import TableInventory


class InventoryGroupNotFoundError(RuntimeError):
    """Raised when an inventory group has no rows in table_inventory."""


class InventoryMembershipConflictError(RuntimeError):
    """Raised when inserting a (group, database, table) row that already exists."""


class InventoryMembershipNotFoundError(RuntimeError):
    """Raised when removing a (group, database, table) row that does not exist."""


def list_groups(session: Session, cluster_id: int) -> list[dict]:
    """List every inventory group with its table-membership count."""
    rows = session.execute(
        select(TableInventory.inventory_group, func.count())
        .where(TableInventory.cluster_id == cluster_id)
        .group_by(TableInventory.inventory_group)
        .order_by(TableInventory.inventory_group)
    ).all()
    return [{"name": name, "table_count": count} for name, count in rows]


def group_exists(session: Session, cluster_id: int, group_name: str) -> bool:
    """Return whether `group_name` has at least one row in table_inventory."""
    return (
        session.scalars(
            select(TableInventory.id)
            .where(TableInventory.cluster_id == cluster_id, TableInventory.inventory_group == group_name)
            .limit(1)
        ).first()
        is not None
    )


def get_group(session: Session, cluster_id: int, group_name: str) -> list[dict]:
    """Return every membership for `group_name`, or `[]` if it has none.

    The caller decides whether an empty result means "unknown group" (404).
    """
    rows = session.scalars(
        select(TableInventory)
        .where(TableInventory.cluster_id == cluster_id, TableInventory.inventory_group == group_name)
        .order_by(TableInventory.database_name, TableInventory.table_name)
    )
    return [
        {
            "database": row.database_name,
            "table": row.table_name,
            "created_at": str(row.created_at),
            "updated_at": str(row.updated_at),
        }
        for row in rows
    ]


def _membership_exists(session: Session, cluster_id: int, group_name: str, database_name: str, table_name: str) -> bool:
    return (
        session.scalars(
            select(TableInventory.id)
            .where(
                TableInventory.cluster_id == cluster_id,
                TableInventory.inventory_group == group_name,
                TableInventory.database_name == database_name,
                TableInventory.table_name == table_name,
            )
            .limit(1)
        ).first()
        is not None
    )


def add_membership(session: Session, cluster_id: int, group_name: str, database_name: str, table_name: str) -> dict:
    """Insert a single (group, database, table) membership row.

    Raises:
        InventoryMembershipConflictError: If that exact membership already exists.
    """
    if _membership_exists(session, cluster_id, group_name, database_name, table_name):
        raise InventoryMembershipConflictError(
            f"Membership ({group_name}, {database_name}, {table_name}) already exists"
        )

    session.add(
        TableInventory(
            cluster_id=cluster_id,
            inventory_group=group_name,
            database_name=database_name,
            table_name=table_name,
        )
    )
    session.flush()
    return {"group": group_name, "database": database_name, "table": table_name}


def add_memberships_bulk(
    session: Session, cluster_id: int, group_name: str, entries: list[tuple[str, str]]
) -> list[dict]:
    """Add several (database, table) memberships to `group_name`.

    Matches `bootstrap_table_inventory`'s existing idempotency: entries that
    already exist are silently skipped rather than raising, so partial
    success across the loop is acceptable and a retry is safe.
    """
    added = []
    for database_name, table_name in entries:
        try:
            added.append(add_membership(session, cluster_id, group_name, database_name, table_name))
        except InventoryMembershipConflictError:
            continue
    return added


def remove_membership(session: Session, cluster_id: int, group_name: str, database_name: str, table_name: str) -> None:
    """Remove a single (group, database, table) membership row.

    Raises:
        InventoryMembershipNotFoundError: If that membership does not exist.
    """
    row = session.scalars(
        select(TableInventory).where(
            TableInventory.cluster_id == cluster_id,
            TableInventory.inventory_group == group_name,
            TableInventory.database_name == database_name,
            TableInventory.table_name == table_name,
        )
    ).one_or_none()
    if row is None:
        raise InventoryMembershipNotFoundError(
            f"Membership ({group_name}, {database_name}, {table_name}) not found"
        )

    session.delete(row)
    session.flush()


def delete_group(session: Session, cluster_id: int, group_name: str) -> int:
    """Delete every membership row for `group_name`.

    Raises:
        InventoryGroupNotFoundError: If the group has zero rows.

    Returns:
        The number of rows deleted.
    """
    rows = session.scalars(
        select(TableInventory).where(
            TableInventory.cluster_id == cluster_id, TableInventory.inventory_group == group_name
        )
    ).all()
    if not rows:
        raise InventoryGroupNotFoundError(f"Inventory group '{group_name}' not found")

    count = len(rows)
    for row in rows:
        session.delete(row)
    session.flush()
    return count


def bootstrap_table_inventory(session: Session, cluster_id: int, entries: list[tuple[str, str, str]]) -> None:
    """Bootstrap table_inventory rows from configuration (used by `cli.py init`).

    Args:
        session: SQLite metastore session
        cluster_id: Cluster these entries belong to
        entries: List of (group, database, table) tuples
    """
    if not entries:
        return

    for group, database, table in entries:
        try:
            add_membership(session, cluster_id, group, database, table)
        except InventoryMembershipConflictError:
            # Re-running init only adds new rows - an existing entry is a no-op.
            continue

    logger.success(f"table_inventory bootstrapped with {len(entries)} entries")
