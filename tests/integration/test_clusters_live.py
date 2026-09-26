"""Live integration tests for the `/cluster{,s}/...` endpoints.

Cluster registration itself is pure metadata-store CRUD, and that surface is
already fully covered against a mocked connection in
`tests/unit/service/test_api_clusters.py`. What only a live StarRocks
instance can prove is that `/cluster/{id}/verify` and `/clusters/verify`
actually succeed or fail against a real connection - see
`tests/integration/conftest.py` for connection details and the whole-module
skip behavior shared with the other live tests.
"""

import uuid

import pytest


@pytest.fixture
def it_cluster_name():
    """A unique cluster name so repeated runs never collide."""
    return f"it_cluster_test_{uuid.uuid4().hex[:8]}"


class TestClusterLifecycle:
    """register -> list -> get -> verify -> update -> verify -> delete against a live StarRocks."""

    def test_register_list_and_get_cluster(self, api_client, it_cluster_name, require_live_starrocks):
        from .conftest import STARROCKS_HOST, STARROCKS_PASSWORD, STARROCKS_PORT, STARROCKS_USER

        create_response = api_client.post(
            "/cluster",
            json={
                "name": it_cluster_name,
                "host": STARROCKS_HOST,
                "port": STARROCKS_PORT,
                "user": STARROCKS_USER,
                "password": STARROCKS_PASSWORD,
            },
        )
        assert create_response.status_code == 201, create_response.text
        created = create_response.json()
        assert created["name"] == it_cluster_name
        assert "password" not in created
        cluster_id = created["id"]

        list_response = api_client.get("/clusters")
        assert list_response.status_code == 200
        assert any(cluster["id"] == cluster_id for cluster in list_response.json())

        get_response = api_client.get(f"/cluster/{cluster_id}")
        assert get_response.status_code == 200
        assert get_response.json()["name"] == it_cluster_name

    def test_verify_registered_cluster_succeeds(self, api_client, it_cluster_name, require_live_starrocks):
        from .conftest import STARROCKS_HOST, STARROCKS_PASSWORD, STARROCKS_PORT, STARROCKS_USER

        created = api_client.post(
            "/cluster",
            json={
                "name": it_cluster_name,
                "host": STARROCKS_HOST,
                "port": STARROCKS_PORT,
                "user": STARROCKS_USER,
                "password": STARROCKS_PASSWORD,
            },
        ).json()

        response = api_client.get(f"/cluster/{created['id']}/verify")

        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_verify_submitted_params_succeeds(self, api_client, require_live_starrocks):
        from .conftest import STARROCKS_HOST, STARROCKS_PASSWORD, STARROCKS_PORT, STARROCKS_USER

        response = api_client.post(
            "/clusters/verify",
            json={
                "host": STARROCKS_HOST,
                "port": STARROCKS_PORT,
                "user": STARROCKS_USER,
                "password": STARROCKS_PASSWORD,
            },
        )

        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_verify_fails_with_wrong_password(self, api_client, it_cluster_name, require_live_starrocks):
        from .conftest import STARROCKS_HOST, STARROCKS_PORT, STARROCKS_USER

        created = api_client.post(
            "/cluster",
            json={
                "name": it_cluster_name,
                "host": STARROCKS_HOST,
                "port": STARROCKS_PORT,
                "user": STARROCKS_USER,
                "password": "definitely-not-the-real-password",
            },
        ).json()

        response = api_client.get(f"/cluster/{created['id']}/verify")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert "definitely-not-the-real-password" not in body["message"]

    def test_update_cluster_changes_which_connection_verify_uses(
        self, api_client, it_cluster_name, require_live_starrocks
    ):
        from .conftest import STARROCKS_HOST, STARROCKS_PASSWORD, STARROCKS_PORT, STARROCKS_USER

        created = api_client.post(
            "/cluster",
            json={
                "name": it_cluster_name,
                "host": STARROCKS_HOST,
                "port": STARROCKS_PORT,
                "user": STARROCKS_USER,
                "password": "definitely-not-the-real-password",
            },
        ).json()
        cluster_id = created["id"]

        broken_verify = api_client.get(f"/cluster/{cluster_id}/verify")
        assert broken_verify.json()["success"] is False

        update_response = api_client.patch(
            f"/cluster/{cluster_id}", json={"password": STARROCKS_PASSWORD}
        )
        assert update_response.status_code == 200

        fixed_verify = api_client.get(f"/cluster/{cluster_id}/verify")
        assert fixed_verify.json()["success"] is True

    def test_delete_cluster(self, api_client, it_cluster_name, require_live_starrocks):
        from .conftest import STARROCKS_HOST, STARROCKS_PASSWORD, STARROCKS_PORT, STARROCKS_USER

        created = api_client.post(
            "/cluster",
            json={
                "name": it_cluster_name,
                "host": STARROCKS_HOST,
                "port": STARROCKS_PORT,
                "user": STARROCKS_USER,
                "password": STARROCKS_PASSWORD,
            },
        ).json()

        delete_response = api_client.delete(f"/cluster/{created['id']}")
        assert delete_response.status_code == 204

        get_after_delete = api_client.get(f"/cluster/{created['id']}")
        assert get_after_delete.status_code == 404


class TestClusterUnknown:
    def test_get_unknown_cluster_is_404(self, api_client, require_live_starrocks):
        response = api_client.get("/cluster/999999")

        assert response.status_code == 404

    def test_verify_unknown_cluster_is_404(self, api_client, require_live_starrocks):
        response = api_client.get("/cluster/999999/verify")

        assert response.status_code == 404
