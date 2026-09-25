"""Inventory group routes, scoped to a registered cluster.

Per specs/api-inventory-groups, inventory group listing/creation/membership
management are live pass-throughs to the target StarRocks cluster - no
local state is introduced, matching `repositories.py`'s pattern.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ... import inventory_groups
from ..auth import require_api_key
from ..deps import get_db
from ..schemas import (
    InventoryGroupCreate,
    InventoryGroupRead,
    InventoryGroupSummary,
    InventoryMembershipCreate,
    InventoryMembershipRead,
)
from ._cluster_connect import connect_or_503, get_cluster_or_404

router = APIRouter(tags=["inventory-groups"], dependencies=[Depends(require_api_key)])


@router.get(
    "/cluster/{cluster_id}/inventory-groups",
    response_model=list[InventoryGroupSummary],
)
def list_inventory_groups(cluster_id: int, db: Session = Depends(get_db)) -> list[dict]:
    cluster = get_cluster_or_404(db, cluster_id)

    database = connect_or_503(cluster)
    try:
        return inventory_groups.list_groups(database, cluster.ops_database)
    finally:
        database.close()


@router.post(
    "/cluster/{cluster_id}/inventory-groups",
    response_model=InventoryGroupRead,
    status_code=status.HTTP_201_CREATED,
)
def create_inventory_group(
    cluster_id: int, payload: InventoryGroupCreate, db: Session = Depends(get_db)
) -> dict:
    cluster = get_cluster_or_404(db, cluster_id)

    database = connect_or_503(cluster)
    try:
        if inventory_groups.group_exists(database, payload.name, cluster.ops_database):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Inventory group '{payload.name}' already exists on cluster '{cluster.name}'",
            )

        entries = [(table.database, table.table) for table in payload.tables]
        inventory_groups.add_memberships_bulk(database, payload.name, entries, cluster.ops_database)

        return {
            "name": payload.name,
            "tables": inventory_groups.get_group(database, payload.name, cluster.ops_database),
        }
    finally:
        database.close()


@router.get(
    "/cluster/{cluster_id}/inventory-groups/{group_name}",
    response_model=InventoryGroupRead,
)
def get_inventory_group(cluster_id: int, group_name: str, db: Session = Depends(get_db)) -> dict:
    cluster = get_cluster_or_404(db, cluster_id)

    database = connect_or_503(cluster)
    try:
        tables = inventory_groups.get_group(database, group_name, cluster.ops_database)
        if not tables:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Inventory group '{group_name}' not found on cluster '{cluster.name}'",
            )
        return {"name": group_name, "tables": tables}
    finally:
        database.close()


@router.post(
    "/cluster/{cluster_id}/inventory-groups/{group_name}/tables",
    response_model=InventoryMembershipRead,
    status_code=status.HTTP_201_CREATED,
)
def add_inventory_group_table(
    cluster_id: int,
    group_name: str,
    payload: InventoryMembershipCreate,
    db: Session = Depends(get_db),
) -> dict:
    cluster = get_cluster_or_404(db, cluster_id)

    database = connect_or_503(cluster)
    try:
        try:
            inventory_groups.add_membership(
                database, group_name, payload.database, payload.table, cluster.ops_database
            )
        except inventory_groups.InventoryMembershipConflictError as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e

        for membership in inventory_groups.get_group(database, group_name, cluster.ops_database):
            if membership["database"] == payload.database and membership["table"] == payload.table:
                return membership
        # Defensive fallback - should be unreachable since add_membership just succeeded.
        return {
            "database": payload.database,
            "table": payload.table,
            "created_at": "",
            "updated_at": "",
        }
    finally:
        database.close()


@router.delete(
    "/cluster/{cluster_id}/inventory-groups/{group_name}/tables/{database_name}/{table_name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_inventory_group_table(
    cluster_id: int,
    group_name: str,
    database_name: str,
    table_name: str,
    db: Session = Depends(get_db),
) -> None:
    cluster = get_cluster_or_404(db, cluster_id)

    database = connect_or_503(cluster)
    try:
        try:
            inventory_groups.remove_membership(
                database, group_name, database_name, table_name, cluster.ops_database
            )
        except inventory_groups.InventoryMembershipNotFoundError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    finally:
        database.close()


@router.delete(
    "/cluster/{cluster_id}/inventory-groups/{group_name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_inventory_group(cluster_id: int, group_name: str, db: Session = Depends(get_db)) -> None:
    cluster = get_cluster_or_404(db, cluster_id)

    database = connect_or_503(cluster)
    try:
        try:
            inventory_groups.delete_group(database, group_name, cluster.ops_database)
        except inventory_groups.InventoryGroupNotFoundError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    finally:
        database.close()
