"""Live integration tests for the `/inventor{y,ies}/...` endpoints.

Inventory-group storage itself has no StarRocks or S3 dependency (it reads and
writes only this tool's own SQLite metastore), but the group is always scoped
to a registered cluster - see `tests/integration/conftest.py` for connection
details and the whole-module skip behavior shared with the other live tests.
"""

import uuid

import pytest


@pytest.fixture
def it_inventory_names():
    """A unique cluster/group name pair so repeated runs never collide."""
    suffix = uuid.uuid4().hex[:8]
    return {"cluster": f"it_inventory_test_cluster_{suffix}", "group": f"it_group_{suffix}"}


@pytest.fixture
def registered_cluster(api_client, it_inventory_names, require_live_starrocks):
    """Register the live StarRocks cluster with the API server."""
    from .conftest import STARROCKS_HOST, STARROCKS_PASSWORD, STARROCKS_PORT, STARROCKS_USER

    response = api_client.post(
        "/cluster",
        json={
            "name": it_inventory_names["cluster"],
            "host": STARROCKS_HOST,
            "port": STARROCKS_PORT,
            "user": STARROCKS_USER,
            "password": STARROCKS_PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


class TestInventoryLifecycle:
    """create -> list -> get -> add/remove membership -> delete against a live-registered cluster."""

    def test_create_list_and_get_group(self, api_client, registered_cluster, it_inventory_names):
        cluster_id = registered_cluster
        group_name = it_inventory_names["group"]

        create_response = api_client.post(
            f"/inventories/cluster/{cluster_id}",
            json={"name": group_name, "tables": [{"database": "sales", "table": "*"}]},
        )
        assert create_response.status_code == 201, create_response.text
        created = create_response.json()
        assert created["name"] == group_name
        group_id = created["id"]

        list_response = api_client.get(f"/inventories/cluster/{cluster_id}")
        assert list_response.status_code == 200
        assert any(
            group["id"] == group_id and group["table_count"] == 1
            for group in list_response.json()
        )

        get_response = api_client.get(f"/inventory/cluster/{cluster_id}/group_id/{group_id}")
        assert get_response.status_code == 200
        body = get_response.json()
        assert body["name"] == group_name
        assert [{"database": t["database"], "table": t["table"]} for t in body["tables"]] == [
            {"database": "sales", "table": "*"}
        ]

    def test_create_duplicate_name_is_409(self, api_client, registered_cluster, it_inventory_names):
        cluster_id = registered_cluster
        payload = {"name": it_inventory_names["group"], "tables": [{"database": "sales", "table": "*"}]}

        first = api_client.post(f"/inventories/cluster/{cluster_id}", json=payload)
        assert first.status_code == 201, first.text

        second = api_client.post(f"/inventories/cluster/{cluster_id}", json=payload)
        assert second.status_code == 409

    def test_get_unknown_group_is_404(self, api_client, registered_cluster):
        response = api_client.get(f"/inventory/cluster/{registered_cluster}/group_id/999999")

        assert response.status_code == 404

    def test_add_and_remove_membership(self, api_client, registered_cluster, it_inventory_names):
        cluster_id = registered_cluster
        create_response = api_client.post(
            f"/inventories/cluster/{cluster_id}",
            json={"name": it_inventory_names["group"], "tables": [{"database": "sales", "table": "*"}]},
        )
        group_id = create_response.json()["id"]

        add_response = api_client.post(
            f"/inventory/cluster/{cluster_id}/group_id/{group_id}/tables",
            json={"database": "sales", "table": "orders"},
        )
        assert add_response.status_code == 201, add_response.text

        get_after_add = api_client.get(f"/inventory/cluster/{cluster_id}/group_id/{group_id}")
        assert any(
            t["database"] == "sales" and t["table"] == "orders" for t in get_after_add.json()["tables"]
        )

        duplicate_add = api_client.post(
            f"/inventory/cluster/{cluster_id}/group_id/{group_id}/tables",
            json={"database": "sales", "table": "orders"},
        )
        assert duplicate_add.status_code == 409

        remove_response = api_client.delete(
            f"/inventory/cluster/{cluster_id}/group_id/{group_id}/tables/sales/orders"
        )
        assert remove_response.status_code == 204

        get_after_remove = api_client.get(f"/inventory/cluster/{cluster_id}/group_id/{group_id}")
        assert all(
            not (t["database"] == "sales" and t["table"] == "orders")
            for t in get_after_remove.json()["tables"]
        )

    def test_delete_group(self, api_client, registered_cluster, it_inventory_names):
        cluster_id = registered_cluster
        create_response = api_client.post(
            f"/inventories/cluster/{cluster_id}",
            json={"name": it_inventory_names["group"], "tables": [{"database": "sales", "table": "*"}]},
        )
        group_id = create_response.json()["id"]

        delete_response = api_client.delete(f"/inventory/cluster/{cluster_id}/group_id/{group_id}")
        assert delete_response.status_code == 204

        get_after_delete = api_client.get(f"/inventory/cluster/{cluster_id}/group_id/{group_id}")
        assert get_after_delete.status_code == 404


class TestInventoryUnknownCluster:
    def test_list_against_unregistered_cluster_is_404(self, api_client):
        response = api_client.get("/inventories/cluster/999999")

        assert response.status_code == 404
