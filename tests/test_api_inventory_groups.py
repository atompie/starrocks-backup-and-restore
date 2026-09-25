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


class FakeDB:
    """Stand-in for StarRocksDB that never opens a real socket."""

    def __init__(self, connect_error=None):
        self.connect_error = connect_error
        self.connected = False
        self.closed = False

    def connect(self):
        if self.connect_error:
            raise self.connect_error
        self.connected = True

    def close(self):
        self.closed = True


def _patch_connect(monkeypatch, fake_db):
    from starrocks_br.api.routes import _cluster_connect

    monkeypatch.setattr(_cluster_connect, "connect", lambda cluster: fake_db)


def test_list_inventory_groups_success(api_client, monkeypatch):
    from starrocks_br import inventory_groups as inventory_groups_module

    fake_db = FakeDB()
    _patch_connect(monkeypatch, fake_db)
    monkeypatch.setattr(
        inventory_groups_module,
        "list_groups",
        lambda db, ops_database: [{"name": "prod", "table_count": 2}],
    )

    cluster_id = _create_cluster(api_client)
    response = api_client.get(f"/cluster/{cluster_id}/inventory-groups")

    assert response.status_code == 200
    assert response.json() == [{"name": "prod", "table_count": 2}]
    assert fake_db.closed is True


def test_create_inventory_group_success(api_client, monkeypatch):
    from starrocks_br import inventory_groups as inventory_groups_module

    fake_db = FakeDB()
    _patch_connect(monkeypatch, fake_db)
    monkeypatch.setattr(inventory_groups_module, "group_exists", lambda db, name, ops_database: False)
    monkeypatch.setattr(inventory_groups_module, "add_memberships_bulk", lambda db, name, entries, ops_database: [])
    monkeypatch.setattr(
        inventory_groups_module,
        "get_group",
        lambda db, name, ops_database: [
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

    fake_db = FakeDB()
    _patch_connect(monkeypatch, fake_db)
    monkeypatch.setattr(inventory_groups_module, "group_exists", lambda db, name, ops_database: True)

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

    fake_db = FakeDB()
    _patch_connect(monkeypatch, fake_db)
    monkeypatch.setattr(
        inventory_groups_module,
        "get_group",
        lambda db, name, ops_database: [
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

    fake_db = FakeDB()
    _patch_connect(monkeypatch, fake_db)
    monkeypatch.setattr(inventory_groups_module, "get_group", lambda db, name, ops_database: [])

    cluster_id = _create_cluster(api_client)
    response = api_client.get(f"/cluster/{cluster_id}/inventory-groups/unknown")

    assert response.status_code == 404


def test_add_table_to_group_success(api_client, monkeypatch):
    from starrocks_br import inventory_groups as inventory_groups_module

    fake_db = FakeDB()
    _patch_connect(monkeypatch, fake_db)
    monkeypatch.setattr(
        inventory_groups_module,
        "add_membership",
        lambda db, group, database, table, ops_database: {
            "group": group,
            "database": database,
            "table": table,
        },
    )
    monkeypatch.setattr(
        inventory_groups_module,
        "get_group",
        lambda db, name, ops_database: [
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

    fake_db = FakeDB()
    _patch_connect(monkeypatch, fake_db)

    def _raise_conflict(db, group, database, table, ops_database):
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

    fake_db = FakeDB()
    _patch_connect(monkeypatch, fake_db)
    removed = []
    monkeypatch.setattr(
        inventory_groups_module,
        "remove_membership",
        lambda db, group, database, table, ops_database: removed.append((group, database, table)),
    )

    cluster_id = _create_cluster(api_client)
    response = api_client.delete(f"/cluster/{cluster_id}/inventory-groups/prod/tables/sales_db/orders")

    assert response.status_code == 204
    assert removed == [("prod", "sales_db", "orders")]


def test_remove_nonexistent_table_from_group_is_404(api_client, monkeypatch):
    from starrocks_br import inventory_groups as inventory_groups_module

    fake_db = FakeDB()
    _patch_connect(monkeypatch, fake_db)

    def _raise_not_found(db, group, database, table, ops_database):
        raise InventoryMembershipNotFoundError("not found")

    monkeypatch.setattr(inventory_groups_module, "remove_membership", _raise_not_found)

    cluster_id = _create_cluster(api_client)
    response = api_client.delete(f"/cluster/{cluster_id}/inventory-groups/prod/tables/sales_db/orders")

    assert response.status_code == 404


def test_delete_inventory_group_success(api_client, monkeypatch):
    from starrocks_br import inventory_groups as inventory_groups_module

    fake_db = FakeDB()
    _patch_connect(monkeypatch, fake_db)
    deleted = []
    monkeypatch.setattr(
        inventory_groups_module,
        "delete_group",
        lambda db, name, ops_database: deleted.append(name) or 2,
    )

    cluster_id = _create_cluster(api_client)
    response = api_client.delete(f"/cluster/{cluster_id}/inventory-groups/prod")

    assert response.status_code == 204
    assert deleted == ["prod"]


def test_delete_nonexistent_inventory_group_is_404(api_client, monkeypatch):
    from starrocks_br import inventory_groups as inventory_groups_module

    fake_db = FakeDB()
    _patch_connect(monkeypatch, fake_db)

    def _raise_not_found(db, name, ops_database):
        raise InventoryGroupNotFoundError("not found")

    monkeypatch.setattr(inventory_groups_module, "delete_group", _raise_not_found)

    cluster_id = _create_cluster(api_client)
    response = api_client.delete(f"/cluster/{cluster_id}/inventory-groups/unknown")

    assert response.status_code == 404


def test_inventory_groups_against_unknown_cluster_is_404(api_client):
    response = api_client.get("/cluster/999/inventory-groups")
    assert response.status_code == 404


def test_inventory_groups_unreachable_cluster_is_503(api_client, monkeypatch):
    fake_db = FakeDB(connect_error=RuntimeError("connection refused"))
    _patch_connect(monkeypatch, fake_db)

    cluster_id = _create_cluster(api_client)
    response = api_client.get(f"/cluster/{cluster_id}/inventory-groups")

    assert response.status_code == 503
    assert "connection refused" in response.json()["detail"].lower()
