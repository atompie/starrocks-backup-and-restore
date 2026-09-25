from starrocks_br.inventory_groups import (
    InventoryGroupNotFoundError,
    InventoryMembershipConflictError,
    InventoryMembershipNotFoundError,
)

CLUSTER_PAYLOAD = {
    "name": "prod-eu",
    "host": "sr.internal",
    "port": 9030,
    "user": "backup_svc",
    "password": "s3cret",
    "database": "sales_db",
    "repository": "s3_repo",
}


def _create_cluster(api_client) -> int:
    return api_client.post("/cluster", json=CLUSTER_PAYLOAD).json()["id"]


def test_list_inventory_groups_success(api_client, monkeypatch):
    from starrocks_br import inventory_groups as inventory_groups_module

    monkeypatch.setattr(
        inventory_groups_module,
        "list_groups",
        lambda db, cluster_id: [{"name": "prod", "table_count": 2}],
    )

    cluster_id = _create_cluster(api_client)
    response = api_client.get(f"/cluster/{cluster_id}/inventory-groups")

    assert response.status_code == 200
    assert response.json() == [{"name": "prod", "table_count": 2}]


def test_create_inventory_group_success(api_client, monkeypatch):
    from starrocks_br import inventory_groups as inventory_groups_module

    monkeypatch.setattr(inventory_groups_module, "group_exists", lambda db, cluster_id, name: False)
    monkeypatch.setattr(
        inventory_groups_module, "add_memberships_bulk", lambda db, cluster_id, name, entries: []
    )
    monkeypatch.setattr(
        inventory_groups_module,
        "get_group",
        lambda db, cluster_id, name: [
            {"database": "sales_db", "table": "*", "created_at": "t1", "updated_at": "t1"}
        ],
    )

    cluster_id = _create_cluster(api_client)
    response = api_client.post(
        f"/cluster/{cluster_id}/inventory-groups",
        json={"name": "prod", "tables": [{"database": "sales_db", "table": "*"}]},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "prod"
    assert body["tables"] == [
        {"database": "sales_db", "table": "*", "created_at": "t1", "updated_at": "t1"}
    ]


def test_create_inventory_group_duplicate_is_409(api_client, monkeypatch):
    from starrocks_br import inventory_groups as inventory_groups_module

    monkeypatch.setattr(inventory_groups_module, "group_exists", lambda db, cluster_id, name: True)

    cluster_id = _create_cluster(api_client)
    response = api_client.post(
        f"/cluster/{cluster_id}/inventory-groups",
        json={"name": "prod", "tables": [{"database": "sales_db", "table": "*"}]},
    )

    assert response.status_code == 409


def test_create_inventory_group_requires_at_least_one_table(api_client):
    cluster_id = _create_cluster(api_client)
    response = api_client.post(
        f"/cluster/{cluster_id}/inventory-groups",
        json={"name": "prod", "tables": []},
    )

    assert response.status_code == 422


def test_get_inventory_group_success(api_client, monkeypatch):
    from starrocks_br import inventory_groups as inventory_groups_module

    monkeypatch.setattr(
        inventory_groups_module,
        "get_group",
        lambda db, cluster_id, name: [
            {"database": "sales_db", "table": "orders", "created_at": "t1", "updated_at": "t1"}
        ],
    )

    cluster_id = _create_cluster(api_client)
    response = api_client.get(f"/cluster/{cluster_id}/inventory-groups/prod")

    assert response.status_code == 200
    assert response.json()["name"] == "prod"
    assert len(response.json()["tables"]) == 1


def test_get_unknown_inventory_group_is_404(api_client, monkeypatch):
    from starrocks_br import inventory_groups as inventory_groups_module

    monkeypatch.setattr(inventory_groups_module, "get_group", lambda db, cluster_id, name: [])

    cluster_id = _create_cluster(api_client)
    response = api_client.get(f"/cluster/{cluster_id}/inventory-groups/unknown")

    assert response.status_code == 404


def test_add_table_to_group_success(api_client, monkeypatch):
    from starrocks_br import inventory_groups as inventory_groups_module

    monkeypatch.setattr(
        inventory_groups_module,
        "add_membership",
        lambda db, cluster_id, group, database, table: {
            "group": group,
            "database": database,
            "table": table,
        },
    )
    monkeypatch.setattr(
        inventory_groups_module,
        "get_group",
        lambda db, cluster_id, name: [
            {"database": "sales_db", "table": "orders", "created_at": "t1", "updated_at": "t1"}
        ],
    )

    cluster_id = _create_cluster(api_client)
    response = api_client.post(
        f"/cluster/{cluster_id}/inventory-groups/prod/tables",
        json={"database": "sales_db", "table": "orders"},
    )

    assert response.status_code == 201
    assert response.json() == {
        "database": "sales_db",
        "table": "orders",
        "created_at": "t1",
        "updated_at": "t1",
    }


def test_add_duplicate_table_to_group_is_409(api_client, monkeypatch):
    from starrocks_br import inventory_groups as inventory_groups_module

    def _raise_conflict(db, cluster_id, group, database, table):
        raise InventoryMembershipConflictError("already exists")

    monkeypatch.setattr(inventory_groups_module, "add_membership", _raise_conflict)

    cluster_id = _create_cluster(api_client)
    response = api_client.post(
        f"/cluster/{cluster_id}/inventory-groups/prod/tables",
        json={"database": "sales_db", "table": "orders"},
    )

    assert response.status_code == 409


def test_remove_table_from_group_success(api_client, monkeypatch):
    from starrocks_br import inventory_groups as inventory_groups_module

    removed = []
    monkeypatch.setattr(
        inventory_groups_module,
        "remove_membership",
        lambda db, cluster_id, group, database, table: removed.append((group, database, table)),
    )

    cluster_id = _create_cluster(api_client)
    response = api_client.delete(f"/cluster/{cluster_id}/inventory-groups/prod/tables/sales_db/orders")

    assert response.status_code == 204
    assert removed == [("prod", "sales_db", "orders")]


def test_remove_nonexistent_table_from_group_is_404(api_client, monkeypatch):
    from starrocks_br import inventory_groups as inventory_groups_module

    def _raise_not_found(db, cluster_id, group, database, table):
        raise InventoryMembershipNotFoundError("not found")

    monkeypatch.setattr(inventory_groups_module, "remove_membership", _raise_not_found)

    cluster_id = _create_cluster(api_client)
    response = api_client.delete(f"/cluster/{cluster_id}/inventory-groups/prod/tables/sales_db/orders")

    assert response.status_code == 404


def test_delete_inventory_group_success(api_client, monkeypatch):
    from starrocks_br import inventory_groups as inventory_groups_module

    deleted = []
    monkeypatch.setattr(
        inventory_groups_module,
        "delete_group",
        lambda db, cluster_id, name: deleted.append(name) or 2,
    )

    cluster_id = _create_cluster(api_client)
    response = api_client.delete(f"/cluster/{cluster_id}/inventory-groups/prod")

    assert response.status_code == 204
    assert deleted == ["prod"]


def test_delete_nonexistent_inventory_group_is_404(api_client, monkeypatch):
    from starrocks_br import inventory_groups as inventory_groups_module

    def _raise_not_found(db, cluster_id, name):
        raise InventoryGroupNotFoundError("not found")

    monkeypatch.setattr(inventory_groups_module, "delete_group", _raise_not_found)

    cluster_id = _create_cluster(api_client)
    response = api_client.delete(f"/cluster/{cluster_id}/inventory-groups/unknown")

    assert response.status_code == 404


def test_inventory_groups_against_unknown_cluster_is_404(api_client):
    response = api_client.get("/cluster/999/inventory-groups")
    assert response.status_code == 404
