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
    response = api_client.post("/cluster", json=CLUSTER_PAYLOAD)

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "prod-eu"
    assert "password" not in body
    assert "password_encrypted" not in body


def test_create_cluster_ignores_ops_database_field(api_client):
    """`ops_database` was removed from the user-facing surface entirely - a client
    still sending it (e.g. an old CLI) must be silently ignored, not echoed back
    or rejected, since ClusterCreate has no `extra="forbid"` config."""
    payload = dict(CLUSTER_PAYLOAD, name="legacy-client-cluster", ops_database="custom_ops")

    response = api_client.post("/cluster", json=payload)

    assert response.status_code == 201
    assert "ops_database" not in response.json()


def test_create_cluster_duplicate_name_conflicts(api_client):
    api_client.post("/cluster", json=CLUSTER_PAYLOAD)
    response = api_client.post("/cluster", json=CLUSTER_PAYLOAD)

    assert response.status_code == 409


def test_create_cluster_allows_empty_password(api_client):
    """StarRocks allows an empty password (e.g. local dev root); the API must too.

    Regression test: found during the live smoke-test against a real StarRocks
    instance, where the registered root user had no password.
    """
    payload = dict(CLUSTER_PAYLOAD, name="empty-pw-cluster", password="")

    response = api_client.post("/cluster", json=payload)

    assert response.status_code == 201


def test_create_cluster_missing_field_is_422(api_client):
    incomplete = dict(CLUSTER_PAYLOAD)
    del incomplete["host"]

    response = api_client.post("/cluster", json=incomplete)

    assert response.status_code == 422


def test_list_and_get_cluster_excludes_password(api_client):
    created = api_client.post("/cluster", json=CLUSTER_PAYLOAD).json()

    listing = api_client.get("/clusters").json()
    assert len(listing) == 1
    assert "password" not in listing[0]

    fetched = api_client.get(f"/cluster/{created['id']}").json()
    assert fetched["name"] == "prod-eu"
    assert "password" not in fetched


def test_get_unknown_cluster_404(api_client):
    response = api_client.get("/cluster/999")
    assert response.status_code == 404


def test_update_cluster_changes_connection_fields(api_client):
    created = api_client.post("/cluster", json=CLUSTER_PAYLOAD).json()

    response = api_client.patch(f"/cluster/{created['id']}", json={"host": "new-host"})

    assert response.status_code == 200
    assert response.json()["host"] == "new-host"


def test_delete_idle_cluster_succeeds(api_client):
    created = api_client.post("/cluster", json=CLUSTER_PAYLOAD).json()

    response = api_client.delete(f"/cluster/{created['id']}")

    assert response.status_code == 204
    assert api_client.get(f"/cluster/{created['id']}").status_code == 404


def test_delete_cluster_blocked_by_active_job(api_client, monkeypatch):
    from starrocks_br import inventory_groups
    from starrocks_br.jobs import handlers

    def slow_handler(cluster, params, on_progress=None):
        import time

        time.sleep(0.3)
        return {}

    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", slow_handler)
    monkeypatch.setattr(inventory_groups, "group_exists", lambda db, cluster_id, group_id: True)

    created = api_client.post("/cluster", json=CLUSTER_PAYLOAD).json()
    api_client.post(f"/backup/manual/full/cluster/{created['id']}", json={"group_id": 1})

    response = api_client.delete(f"/cluster/{created['id']}")

    assert response.status_code == 409


VERIFY_PAYLOAD = {
    "host": "sr.internal",
    "port": 9030,
    "user": "backup_svc",
    "password": "s3cret",
    "database": "sales_db",
}


def _patch_verify_connection(monkeypatch, result):
    from starrocks_br.api.routes import clusters as clusters_module

    calls = []

    def fake_verify_connection(*, host, port, user, password, database):
        calls.append(
            {"host": host, "port": port, "user": user, "password": password, "database": database}
        )
        return result

    monkeypatch.setattr(clusters_module, "_verify_connection", fake_verify_connection)
    return calls


def test_verify_connection_params_succeeds(api_client, monkeypatch):
    from starrocks_br.api.schemas import ClusterVerifyResponse

    calls = _patch_verify_connection(
        monkeypatch, ClusterVerifyResponse(success=True, message="Connection successful")
    )

    response = api_client.post("/clusters/verify", json=VERIFY_PAYLOAD)

    assert response.status_code == 200
    assert response.json() == {"success": True, "message": "Connection successful"}
    assert calls == [
        {
            "host": "sr.internal",
            "port": 9030,
            "user": "backup_svc",
            "password": "s3cret",
            "database": "sales_db",
        }
    ]


def test_verify_connection_params_fails(api_client, monkeypatch):
    from starrocks_br.api.schemas import ClusterVerifyResponse

    _patch_verify_connection(
        monkeypatch, ClusterVerifyResponse(success=False, message="Connection failed: Access denied")
    )

    response = api_client.post("/clusters/verify", json=VERIFY_PAYLOAD)

    assert response.status_code == 200
    assert response.json() == {"success": False, "message": "Connection failed: Access denied"}


def test_verify_connection_params_null_database(api_client, monkeypatch):
    from starrocks_br.api.schemas import ClusterVerifyResponse

    calls = _patch_verify_connection(
        monkeypatch, ClusterVerifyResponse(success=True, message="Connection successful")
    )
    payload = dict(VERIFY_PAYLOAD)
    del payload["database"]

    response = api_client.post("/clusters/verify", json=payload)

    assert response.status_code == 200
    assert calls[0]["database"] is None


def test_verify_connection_params_missing_field_is_422(api_client):
    incomplete = dict(VERIFY_PAYLOAD)
    del incomplete["host"]

    response = api_client.post("/clusters/verify", json=incomplete)

    assert response.status_code == 422


def test_verify_cluster_succeeds(api_client, monkeypatch):
    from starrocks_br.api.schemas import ClusterVerifyResponse

    calls = _patch_verify_connection(
        monkeypatch, ClusterVerifyResponse(success=True, message="Connection successful")
    )
    created = api_client.post("/cluster", json=CLUSTER_PAYLOAD).json()

    response = api_client.get(f"/cluster/{created['id']}/verify")

    assert response.status_code == 200
    assert response.json() == {"success": True, "message": "Connection successful"}
    assert calls == [
        {
            "host": "sr.internal",
            "port": 9030,
            "user": "backup_svc",
            "password": "s3cret",
            "database": "sales_db",
        }
    ]


def test_verify_cluster_fails(api_client, monkeypatch):
    from starrocks_br.api.schemas import ClusterVerifyResponse

    _patch_verify_connection(
        monkeypatch,
        ClusterVerifyResponse(success=False, message="Connection failed: Can't connect to MySQL server"),
    )
    created = api_client.post("/cluster", json=CLUSTER_PAYLOAD).json()

    response = api_client.get(f"/cluster/{created['id']}/verify")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "Can't connect" in body["message"]


def test_verify_cluster_null_database(api_client, monkeypatch):
    """Stored clusters always have a database today (registry requires one), but
    the verify code path must still handle a null database gracefully - e.g. if
    that constraint is relaxed later. Patch `_get_cluster_or_404` to hand back a
    cluster row with `database=None` without persisting it, since the real
    registry rejects a null database at creation."""
    from starrocks_br.api.routes import clusters as clusters_module
    from starrocks_br.api.schemas import ClusterVerifyResponse
    from starrocks_br.store.models import Cluster

    calls = _patch_verify_connection(
        monkeypatch, ClusterVerifyResponse(success=True, message="Connection successful")
    )

    fake_cluster = Cluster(
        id=1,
        name="no-db-cluster",
        host="sr.internal",
        port=9030,
        user="backup_svc",
        password_encrypted=clusters_module.encrypt_password("s3cret"),
        database=None,
        repository="s3_repo",
    )
    monkeypatch.setattr(clusters_module, "_get_cluster_or_404", lambda db, cluster_id: fake_cluster)

    response = api_client.get("/cluster/1/verify")

    assert response.status_code == 200
    assert calls[0]["database"] is None


def test_verify_unknown_cluster_404(api_client):
    response = api_client.get("/cluster/999/verify")
    assert response.status_code == 404


def test_verify_cluster_undecryptable_password_returns_graceful_failure(api_client, monkeypatch):
    """Regression: a stored password that can't be decrypted (e.g. the
    STARROCKS_BR_DB_ENCRYPTION_KEY was rotated after the cluster was
    registered) must not crash the endpoint with an unhandled 500 - it's a
    connection-verification failure like any other."""
    from starrocks_br.api.routes import clusters as clusters_module

    created = api_client.post("/cluster", json=CLUSTER_PAYLOAD).json()

    def failing_decrypt(token):
        raise ValueError("Stored password could not be decrypted (wrong or rotated key)")

    monkeypatch.setattr(clusters_module, "decrypt_password", failing_decrypt)

    response = api_client.get(f"/cluster/{created['id']}/verify")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "decrypted" in body["message"]


def test_verify_never_exposes_password(api_client, monkeypatch):
    from starrocks_br.api.schemas import ClusterVerifyResponse

    _patch_verify_connection(
        monkeypatch,
        ClusterVerifyResponse(success=False, message="Connection failed: Access denied for user"),
    )

    response = api_client.post("/clusters/verify", json=VERIFY_PAYLOAD)

    assert "s3cret" not in response.text

    created = api_client.post("/cluster", json=CLUSTER_PAYLOAD).json()
    response = api_client.get(f"/cluster/{created['id']}/verify")

    assert "s3cret" not in response.text
