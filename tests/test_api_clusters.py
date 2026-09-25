# Copyright 2025 deep-bi
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

CLUSTER_PAYLOAD = {
    "name": "prod-eu",
    "host": "sr.internal",
    "port": 9030,
    "user": "backup_svc",
    "password": "s3cret",
    "database": "sales_db",
    "repository": "s3_repo",
}


def test_create_cluster_succeeds_and_hides_password(api_client):
    response = api_client.post("/clusters", json=CLUSTER_PAYLOAD)

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "prod-eu"
    assert "password" not in body
    assert "password_encrypted" not in body


def test_create_cluster_duplicate_name_conflicts(api_client):
    api_client.post("/clusters", json=CLUSTER_PAYLOAD)
    response = api_client.post("/clusters", json=CLUSTER_PAYLOAD)

    assert response.status_code == 409


def test_create_cluster_allows_empty_password(api_client):
    """StarRocks allows an empty password (e.g. local dev root); the API must too.

    Regression test: found during the live smoke-test against a real StarRocks
    instance, where the registered root user had no password.
    """
    payload = dict(CLUSTER_PAYLOAD, name="empty-pw-cluster", password="")

    response = api_client.post("/clusters", json=payload)

    assert response.status_code == 201


def test_create_cluster_missing_field_is_422(api_client):
    incomplete = dict(CLUSTER_PAYLOAD)
    del incomplete["host"]

    response = api_client.post("/clusters", json=incomplete)

    assert response.status_code == 422


def test_list_and_get_cluster_excludes_password(api_client):
    created = api_client.post("/clusters", json=CLUSTER_PAYLOAD).json()

    listing = api_client.get("/clusters").json()
    assert len(listing) == 1
    assert "password" not in listing[0]

    fetched = api_client.get(f"/clusters/{created['id']}").json()
    assert fetched["name"] == "prod-eu"
    assert "password" not in fetched


def test_get_unknown_cluster_404(api_client):
    response = api_client.get("/clusters/999")
    assert response.status_code == 404


def test_update_cluster_changes_connection_fields(api_client):
    created = api_client.post("/clusters", json=CLUSTER_PAYLOAD).json()

    response = api_client.patch(f"/clusters/{created['id']}", json={"host": "new-host"})

    assert response.status_code == 200
    assert response.json()["host"] == "new-host"


def test_delete_idle_cluster_succeeds(api_client):
    created = api_client.post("/clusters", json=CLUSTER_PAYLOAD).json()

    response = api_client.delete(f"/clusters/{created['id']}")

    assert response.status_code == 204
    assert api_client.get(f"/clusters/{created['id']}").status_code == 404


def test_delete_cluster_blocked_by_active_job(api_client, monkeypatch):
    from starrocks_br.jobs import handlers

    def slow_handler(cluster, params, on_progress=None):
        import time

        time.sleep(0.3)
        return {}

    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", slow_handler)

    created = api_client.post("/clusters", json=CLUSTER_PAYLOAD).json()
    api_client.post(f"/clusters/{created['id']}/backups/full", json={"group": "g1"})

    response = api_client.delete(f"/clusters/{created['id']}")

    assert response.status_code == 409
