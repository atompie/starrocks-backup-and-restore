"""Inventory group CRUD, scoped to a registered cluster.

An inventory group is a row in `store.models.InventoryGroup` (id, cluster_id,
name), with its table/database memberships stored in `table_inventory` rows
that reference it via `inventory_group_id` - not in a StarRocks database.
`table_inventory` has a `UniqueConstraint` on (cluster_id, inventory_group_id,
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
from .store.models import InventoryGroup, Schedule, TableInventory


class InventoryGroupNotFoundError(RuntimeError):
    """Raised when an inventory group id does not exist on a cluster."""


class InventoryGroupAlreadyExistsError(RuntimeError):
    """Raised when creating a group whose name already exists on a cluster."""


class InventoryGroupInUseError(RuntimeError):
    """Raised when deleting a group that a schedule still references."""


class InventoryMembershipConflictError(RuntimeError):
    """Raised when inserting a (group, database, table) row that already exists."""


class InventoryMembershipNotFoundError(RuntimeError):
    """Raised when removing a (group, database, table) row that does not exist."""


def get_group_id_by_name(session: Session, cluster_id: int, name: str) -> int:
    """Resolve a human-readable group name to its id, scoped to a cluster.

    Raises:
        InventoryGroupNotFoundError: If no group with that name exists on this cluster.
    """
    group_id = session.scalars(
        select(InventoryGroup.id).where(InventoryGroup.cluster_id == cluster_id, InventoryGroup.name == name)
    ).first()
    if group_id is None:
        raise InventoryGroupNotFoundError(f"Inventory group '{name}' not found on this cluster")
    return group_id


def _get_or_create_group_id(session: Session, cluster_id: int, name: str) -> int:
    group_id = session.scalars(
        select(InventoryGroup.id).where(InventoryGroup.cluster_id == cluster_id, InventoryGroup.name == name)
    ).first()
    if group_id is not None:
        return group_id

    group = InventoryGroup(cluster_id=cluster_id, name=name)
    session.add(group)
    session.flush()
    return group.id


def group_exists(session: Session, cluster_id: int, group_id: int) -> bool:
    """Return whether `group_id` exists on this cluster."""
    return (
        session.scalars(
            select(InventoryGroup.id)
            .where(InventoryGroup.cluster_id == cluster_id, InventoryGroup.id == group_id)
            .limit(1)
        ).first()
        is not None
    )


def list_groups(session: Session, cluster_id: int) -> list[dict]:
    """List every inventory group with its id, name, and table-membership count."""
    rows = session.execute(
        select(InventoryGroup.id, InventoryGroup.name, func.count(TableInventory.id))
        .outerjoin(TableInventory, TableInventory.inventory_group_id == InventoryGroup.id)
        .where(InventoryGroup.cluster_id == cluster_id)
        .group_by(InventoryGroup.id, InventoryGroup.name)
        .order_by(InventoryGroup.name)
    ).all()
    return [{"id": group_id, "name": name, "table_count": count} for group_id, name, count in rows]


def get_group(session: Session, cluster_id: int, group_id: int) -> list[dict]:
    """Return every membership for `group_id`.

    Raises:
        InventoryGroupNotFoundError: If the group id does not exist on this cluster.
    """
    if not group_exists(session, cluster_id, group_id):
        raise InventoryGroupNotFoundError(f"Inventory group id {group_id} not found on this cluster")

    rows = session.scalars(
        select(TableInventory)
        .where(TableInventory.cluster_id == cluster_id, TableInventory.inventory_group_id == group_id)
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


def _membership_exists(session: Session, cluster_id: int, group_id: int, database_name: str, table_name: str) -> bool:
    return (
        session.scalars(
            select(TableInventory.id)
            .where(
                TableInventory.cluster_id == cluster_id,
                TableInventory.inventory_group_id == group_id,
                TableInventory.database_name == database_name,
                TableInventory.table_name == table_name,
            )
            .limit(1)
        ).first()
        is not None
    )


def add_membership(session: Session, cluster_id: int, group_id: int, database_name: str, table_name: str) -> dict:
    """Insert a single (group, database, table) membership row.

    Raises:
        InventoryMembershipConflictError: If that exact membership already exists.
    """
    if _membership_exists(session, cluster_id, group_id, database_name, table_name):
        raise InventoryMembershipConflictError(
            f"Membership (group {group_id}, {database_name}, {table_name}) already exists"
        )

    session.add(
        TableInventory(
            cluster_id=cluster_id,
            inventory_group_id=group_id,
            database_name=database_name,
            table_name=table_name,
        )
    )
    session.flush()
    return {"group_id": group_id, "database": database_name, "table": table_name}


def add_memberships_bulk(
    session: Session, cluster_id: int, group_id: int, entries: list[tuple[str, str]]
) -> list[dict]:
    """Add several (database, table) memberships to `group_id`.

    Matches `bootstrap_table_inventory`'s existing idempotency: entries that
    already exist are silently skipped rather than raising, so partial
    success across the loop is acceptable and a retry is safe.
    """
    added = []
    for database_name, table_name in entries:
        try:
            added.append(add_membership(session, cluster_id, group_id, database_name, table_name))
        except InventoryMembershipConflictError:
            continue
    return added


def create_group(session: Session, cluster_id: int, name: str, entries: list[tuple[str, str]]) -> dict:
    """Create a new inventory group with an initial set of table memberships.

    Raises:
        InventoryGroupAlreadyExistsError: If `name` already exists on this cluster.
    """
    existing = session.scalars(
        select(InventoryGroup.id).where(InventoryGroup.cluster_id == cluster_id, InventoryGroup.name == name)
    ).first()
    if existing is not None:
        raise InventoryGroupAlreadyExistsError(f"Inventory group '{name}' already exists on this cluster")

    group = InventoryGroup(cluster_id=cluster_id, name=name)
    session.add(group)
    session.flush()
    add_memberships_bulk(session, cluster_id, group.id, entries)
    return {"id": group.id, "name": name}


def remove_membership(session: Session, cluster_id: int, group_id: int, database_name: str, table_name: str) -> None:
    """Remove a single (group, database, table) membership row.

    Raises:
        InventoryMembershipNotFoundError: If that membership does not exist.
    """
    row = session.scalars(
        select(TableInventory).where(
            TableInventory.cluster_id == cluster_id,
            TableInventory.inventory_group_id == group_id,
            TableInventory.database_name == database_name,
            TableInventory.table_name == table_name,
        )
    ).one_or_none()
    if row is None:
        raise InventoryMembershipNotFoundError(
            f"Membership (group {group_id}, {database_name}, {table_name}) not found"
        )

    session.delete(row)
    session.flush()


def delete_group(session: Session, cluster_id: int, group_id: int) -> int:
    """Delete an inventory group and all of its table memberships.

    Raises:
        InventoryGroupNotFoundError: If the group id does not exist on this cluster.
        InventoryGroupInUseError: If any schedule still references this group id.

    Returns:
        The number of table_inventory rows deleted.
    """
    group = session.get(InventoryGroup, group_id)
    if group is None or group.cluster_id != cluster_id:
        raise InventoryGroupNotFoundError(f"Inventory group id {group_id} not found on this cluster")

    blocking_schedule_ids = session.scalars(
        select(Schedule.id).where(Schedule.inventory_group_id == group_id)
    ).all()
    if blocking_schedule_ids:
        raise InventoryGroupInUseError(
            f"Inventory group id {group_id} is referenced by schedule id(s) "
            f"{sorted(blocking_schedule_ids)}; delete or repoint them first"
        )

    count = session.scalar(
        select(func.count())
        .select_from(TableInventory)
        .where(TableInventory.cluster_id == cluster_id, TableInventory.inventory_group_id == group_id)
    )
    session.delete(group)
    session.flush()
    return count


def bootstrap_table_inventory(session: Session, cluster_id: int, entries: list[tuple[str, str, str]]) -> None:
    """Bootstrap table_inventory rows from configuration (used by `cli.py init`).

    Args:
        session: SQLite metastore session
        cluster_id: Cluster these entries belong to
        entries: List of (group name, database, table) tuples
    """
    if not entries:
        return

    for group_name, database, table in entries:
        group_id = _get_or_create_group_id(session, cluster_id, group_name)
        try:
            add_membership(session, cluster_id, group_id, database, table)
        except InventoryMembershipConflictError:
            # Re-running init only adds new rows - an existing entry is a no-op.
            continue

    logger.success(f"table_inventory bootstrapped with {len(entries)} entries")
