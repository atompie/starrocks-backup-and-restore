"""Live integration tests for the `/repositories/...` endpoints.

Unlike `tests/test_api_repositories.py` (which mocks the StarRocks
connection), these tests exercise the real HTTP API against a real StarRocks
cluster and a real S3-compatible store - see `tests/integration/conftest.py`
for connection details and the whole-module skip behavior.
"""

import uuid

import pytest

from .conftest import S3_ACCESS_KEY, S3_BUCKET, S3_ENDPOINT, S3_ENDPOINT_FROM_STARROCKS, S3_SECRET_KEY


@pytest.fixture
def it_repo_names():
    """A unique cluster/repository name pair so repeated runs never collide."""
    suffix = uuid.uuid4().hex[:8]
    return {"cluster": f"it_repo_test_cluster_{suffix}", "repository": f"it_repo_{suffix}"}


@pytest.fixture
def registered_cluster(api_client, it_repo_names, require_live_starrocks):
    """Register the live StarRocks cluster with the API server."""
    from .conftest import STARROCKS_HOST, STARROCKS_PASSWORD, STARROCKS_PORT, STARROCKS_USER

    response = api_client.post(
        "/cluster",
        json={
            "name": it_repo_names["cluster"],
            "host": STARROCKS_HOST,
            "port": STARROCKS_PORT,
            "user": STARROCKS_USER,
            "password": STARROCKS_PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


class TestVerifyRepository:
    """`POST /repositories/verify` talks to S3 directly - no cluster needed."""

    def test_valid_credentials_and_bucket_succeed(self, api_client, require_live_starrocks):
        response = api_client.post(
            "/repositories/verify",
            json={
                "location": f"s3://{S3_BUCKET}/it-verify",
                "access_key": S3_ACCESS_KEY,
                "secret_key": S3_SECRET_KEY,
                "endpoint": S3_ENDPOINT,
            },
        )

        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_wrong_secret_key_fails_without_leaking_it(self, api_client, require_live_starrocks):
        response = api_client.post(
            "/repositories/verify",
            json={
                "location": f"s3://{S3_BUCKET}/it-verify",
                "access_key": S3_ACCESS_KEY,
                "secret_key": "definitely-wrong-secret",
                "endpoint": S3_ENDPOINT,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert "definitely-wrong-secret" not in body["message"]

    def test_nonexistent_bucket_fails(self, api_client, require_live_starrocks):
        response = api_client.post(
            "/repositories/verify",
            json={
                "location": "s3://this-bucket-should-not-exist/it-verify",
                "access_key": S3_ACCESS_KEY,
                "secret_key": S3_SECRET_KEY,
                "endpoint": S3_ENDPOINT,
            },
        )

        assert response.status_code == 200
        assert response.json()["success"] is False


class TestRepositoryLifecycle:
    """create -> list -> delete against a real StarRocks cluster."""

    def test_create_list_and_delete_repository(
        self, api_client, registered_cluster, it_repo_names, require_starrocks_reachable_s3
    ):
        cluster_id = registered_cluster
        repo_name = it_repo_names["repository"]
        location = f"s3://{S3_BUCKET}/it-repo-lifecycle/{repo_name}"

        create_response = api_client.post(
            f"/repositories/cluster/{cluster_id}",
            json={
                "name": repo_name,
                "location": location,
                "access_key": S3_ACCESS_KEY,
                "secret_key": S3_SECRET_KEY,
                "endpoint": S3_ENDPOINT_FROM_STARROCKS,
            },
        )
        assert create_response.status_code == 201, create_response.text
        created = create_response.json()
        assert created["name"] == repo_name
        assert "access_key" not in created
        assert "secret_key" not in created

        list_response = api_client.get(f"/repositories/cluster/{cluster_id}")
        assert list_response.status_code == 200
        assert any(repo["name"] == repo_name for repo in list_response.json())

        delete_response = api_client.delete(f"/repositories/cluster/{cluster_id}/name/{repo_name}")
        assert delete_response.status_code == 204

        list_after_delete = api_client.get(f"/repositories/cluster/{cluster_id}")
        assert all(repo["name"] != repo_name for repo in list_after_delete.json())

    def test_create_duplicate_name_is_409(
        self, api_client, registered_cluster, it_repo_names, require_starrocks_reachable_s3
    ):
        cluster_id = registered_cluster
        repo_name = it_repo_names["repository"]
        payload = {
            "name": repo_name,
            "location": f"s3://{S3_BUCKET}/it-repo-duplicate/{repo_name}",
            "access_key": S3_ACCESS_KEY,
            "secret_key": S3_SECRET_KEY,
            "endpoint": S3_ENDPOINT_FROM_STARROCKS,
        }
        first = api_client.post(f"/repositories/cluster/{cluster_id}", json=payload)
        assert first.status_code == 201, first.text

        second = api_client.post(f"/repositories/cluster/{cluster_id}", json=payload)
        assert second.status_code == 409

        api_client.delete(f"/repositories/cluster/{cluster_id}/name/{repo_name}")

    def test_create_against_unknown_cluster_is_404(self, api_client, it_repo_names, require_live_starrocks):
        response = api_client.post(
            "/repositories/cluster/999999",
            json={
                "name": it_repo_names["repository"],
                "location": f"s3://{S3_BUCKET}/it-repo-unknown-cluster",
                "access_key": S3_ACCESS_KEY,
                "secret_key": S3_SECRET_KEY,
                "endpoint": S3_ENDPOINT_FROM_STARROCKS or S3_ENDPOINT,
            },
        )

        assert response.status_code == 404

    def test_delete_nonexistent_repository_is_404(self, api_client, registered_cluster, require_live_starrocks):
        response = api_client.delete(f"/repositories/cluster/{registered_cluster}/name/never-created-repo")

        assert response.status_code == 404
