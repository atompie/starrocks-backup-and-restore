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


def _create_group(api_client, cluster_id, name="prod", tables=None) -> dict:
    tables = tables if tables is not None else [{"database": "sales_db", "table": "*"}]
    response = api_client.post(
        f"/cluster/{cluster_id}/inventory-groups",
        json={"name": name, "tables": tables},
    )
    assert response.status_code == 201
    return response.json()


def test_list_inventory_groups_success(api_client):
    cluster_id = _create_cluster(api_client)
    created = _create_group(api_client, cluster_id)

    response = api_client.get(f"/cluster/{cluster_id}/inventory-groups")

    assert response.status_code == 200
    assert response.json() == [{"id": created["id"], "name": "prod", "table_count": 1}]


def test_create_inventory_group_success(api_client):
    cluster_id = _create_cluster(api_client)

    response = api_client.post(
        f"/cluster/{cluster_id}/inventory-groups",
        json={"name": "prod", "tables": [{"database": "sales_db", "table": "*"}]},
    )

    assert response.status_code == 201
    body = response.json()
    assert isinstance(body["id"], int)
    assert body["name"] == "prod"
    assert body["tables"] == [
        {
            "database": "sales_db",
            "table": "*",
            "created_at": body["tables"][0]["created_at"],
            "updated_at": body["tables"][0]["updated_at"],
        }
    ]


def test_create_inventory_group_duplicate_is_409(api_client):
    cluster_id = _create_cluster(api_client)
    _create_group(api_client, cluster_id, name="prod")

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


def test_get_inventory_group_success(api_client):
    cluster_id = _create_cluster(api_client)
    created = _create_group(
        api_client, cluster_id, tables=[{"database": "sales_db", "table": "orders"}]
    )

    response = api_client.get(f"/cluster/{cluster_id}/inventory-groups/{created['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]
    assert response.json()["name"] == "prod"
    assert len(response.json()["tables"]) == 1


def test_get_unknown_inventory_group_is_404(api_client):
    cluster_id = _create_cluster(api_client)

    response = api_client.get(f"/cluster/{cluster_id}/inventory-groups/999")

    assert response.status_code == 404


def test_add_table_to_group_success(api_client):
    cluster_id = _create_cluster(api_client)
    created = _create_group(
        api_client, cluster_id, tables=[{"database": "sales_db", "table": "orders"}]
    )

    response = api_client.post(
        f"/cluster/{cluster_id}/inventory-groups/{created['id']}/tables",
        json={"database": "sales_db", "table": "customers"},
    )

    assert response.status_code == 201
    assert response.json()["database"] == "sales_db"
    assert response.json()["table"] == "customers"


def test_add_duplicate_table_to_group_is_409(api_client):
    cluster_id = _create_cluster(api_client)
    created = _create_group(
        api_client, cluster_id, tables=[{"database": "sales_db", "table": "orders"}]
    )

    response = api_client.post(
        f"/cluster/{cluster_id}/inventory-groups/{created['id']}/tables",
        json={"database": "sales_db", "table": "orders"},
    )

    assert response.status_code == 409


def test_remove_table_from_group_success(api_client):
    cluster_id = _create_cluster(api_client)
    created = _create_group(
        api_client, cluster_id, tables=[{"database": "sales_db", "table": "orders"}]
    )

    response = api_client.delete(
        f"/cluster/{cluster_id}/inventory-groups/{created['id']}/tables/sales_db/orders"
    )

    assert response.status_code == 204
    assert api_client.get(f"/cluster/{cluster_id}/inventory-groups/{created['id']}").json()["tables"] == []


def test_remove_nonexistent_table_from_group_is_404(api_client):
    cluster_id = _create_cluster(api_client)
    created = _create_group(
        api_client, cluster_id, tables=[{"database": "sales_db", "table": "orders"}]
    )

    response = api_client.delete(
        f"/cluster/{cluster_id}/inventory-groups/{created['id']}/tables/sales_db/unknown_table"
    )

    assert response.status_code == 404


def test_delete_inventory_group_success(api_client):
    cluster_id = _create_cluster(api_client)
    created = _create_group(api_client, cluster_id)

    response = api_client.delete(f"/cluster/{cluster_id}/inventory-groups/{created['id']}")

    assert response.status_code == 204
    assert api_client.get(f"/cluster/{cluster_id}/inventory-groups").json() == []


def test_delete_nonexistent_inventory_group_is_404(api_client):
    cluster_id = _create_cluster(api_client)

    response = api_client.delete(f"/cluster/{cluster_id}/inventory-groups/999")

    assert response.status_code == 404


def test_delete_inventory_group_referenced_by_schedule_is_409(api_client):
    cluster_id = _create_cluster(api_client)
    created = _create_group(api_client, cluster_id)

    schedule_response = api_client.post(
        f"/cluster/{cluster_id}/schedules",
        json={
            "job_type": "backup_full",
            "inventory_group_id": created["id"],
            "cadence": "0 1 * * *",
        },
    )
    assert schedule_response.status_code == 201

    response = api_client.delete(f"/cluster/{cluster_id}/inventory-groups/{created['id']}")

    assert response.status_code == 409
    assert api_client.get(f"/cluster/{cluster_id}/inventory-groups/{created['id']}").status_code == 200


def test_inventory_groups_against_unknown_cluster_is_404(api_client):
    response = api_client.get("/cluster/999/inventory-groups")
    assert response.status_code == 404
