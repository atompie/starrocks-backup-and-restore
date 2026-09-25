from starrocks_br.repository import RepositoryNotFoundError

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
        self.executed = []

    def connect(self):
        if self.connect_error:
            raise self.connect_error
        self.connected = True

    def close(self):
        self.closed = True

    def execute(self, sql):
        self.executed.append(sql)

    def query(self, sql):
        raise NotImplementedError


def _patch_connect(monkeypatch, fake_db):
    from starrocks_br.api.routes import _cluster_connect

    monkeypatch.setattr(_cluster_connect, "connect", lambda cluster: fake_db)


def test_repositories_router_registered_alongside_others(api_client):
    cluster_id = _create_cluster(api_client)

    # Reachable alongside the existing cluster/job/schedule routers - a
    # connection failure (real DB unreachable in tests) still proves the
    # route is registered and dispatches, unlike a 404 for an unknown route.
    response = api_client.get(f"/cluster/{cluster_id}/repositories")

    assert response.status_code != 404


def test_list_repositories_success(api_client, monkeypatch):
    from starrocks_br import repository as repository_module

    fake_db = FakeDB()
    _patch_connect(monkeypatch, fake_db)
    monkeypatch.setattr(
        repository_module,
        "list_repositories",
        lambda db: [
            {"name": "r1", "location": "s3://b/p", "broker": "", "is_read_only": False, "error": None}
        ],
    )

    cluster_id = _create_cluster(api_client)
    response = api_client.get(f"/cluster/{cluster_id}/repositories")

    assert response.status_code == 200
    assert response.json() == [
        {"name": "r1", "location": "s3://b/p", "broker": "", "is_read_only": False, "error": None}
    ]
    assert fake_db.closed is True


def test_list_repositories_against_unknown_cluster_is_404(api_client):
    response = api_client.get("/cluster/999/repositories")
    assert response.status_code == 404


def test_list_repositories_unreachable_cluster_is_distinguishable_error(api_client, monkeypatch):
    fake_db = FakeDB(connect_error=RuntimeError("connection refused"))
    _patch_connect(monkeypatch, fake_db)

    cluster_id = _create_cluster(api_client)
    response = api_client.get(f"/cluster/{cluster_id}/repositories")

    assert response.status_code == 503
    assert "connection refused" in response.json()["detail"].lower()


def test_create_repository_success(api_client, monkeypatch):
    from starrocks_br import repository as repository_module

    fake_db = FakeDB()
    _patch_connect(monkeypatch, fake_db)
    monkeypatch.setattr(
        repository_module,
        "list_repositories",
        lambda db: [
            {
                "name": "my_repo",
                "location": "s3://bucket/path",
                "broker": "",
                "is_read_only": False,
                "error": None,
            }
        ],
    )

    cluster_id = _create_cluster(api_client)
    response = api_client.post(
        f"/cluster/{cluster_id}/repositories",
        json={
            "name": "my_repo",
            "location": "s3://bucket/path",
            "access_key": "AK",
            "secret_key": "SK",
            "endpoint": "https://s3.amazonaws.com",
        },
    )

    assert response.status_code == 201
    assert response.json()["name"] == "my_repo"
    assert len(fake_db.executed) == 1
    assert "CREATE REPOSITORY" in fake_db.executed[0]


def test_create_repository_duplicate_name_is_409(api_client, monkeypatch):
    fake_db = FakeDB()

    def _raise_execute(sql):
        # Actual StarRocks wording (verified against a live cluster): "exist", not "exists".
        raise RuntimeError("repository with same name already exist: my_repo")

    fake_db.execute = _raise_execute
    _patch_connect(monkeypatch, fake_db)

    cluster_id = _create_cluster(api_client)
    response = api_client.post(
        f"/cluster/{cluster_id}/repositories",
        json={
            "name": "my_repo",
            "location": "s3://bucket/path",
            "access_key": "AK",
            "secret_key": "SK",
            "endpoint": "https://s3.amazonaws.com",
        },
    )

    assert response.status_code == 409


def test_create_repository_against_unknown_cluster_is_404(api_client):
    response = api_client.post(
        "/cluster/999/repositories",
        json={
            "name": "my_repo",
            "location": "s3://bucket/path",
            "access_key": "AK",
            "secret_key": "SK",
            "endpoint": "https://s3.amazonaws.com",
        },
    )
    assert response.status_code == 404


def test_create_repository_missing_required_field_is_422(api_client):
    cluster_id = _create_cluster(api_client)
    response = api_client.post(
        f"/cluster/{cluster_id}/repositories",
        json={"name": "my_repo", "location": "s3://bucket/path"},
    )
    assert response.status_code == 422


def test_create_repository_never_persists_credentials(api_client, monkeypatch):
    from starrocks_br import repository as repository_module
    from starrocks_br.store.models import Cluster, Job

    fake_db = FakeDB()
    _patch_connect(monkeypatch, fake_db)
    monkeypatch.setattr(repository_module, "list_repositories", lambda db: [])

    cluster_id = _create_cluster(api_client)
    api_client.post(
        f"/cluster/{cluster_id}/repositories",
        json={
            "name": "my_repo",
            "location": "s3://bucket/path",
            "access_key": "top-secret-access-key",
            "secret_key": "top-secret-secret-key",
            "endpoint": "https://s3.amazonaws.com",
        },
    )

    from sqlalchemy.orm import Session

    from starrocks_br.store.session import get_engine

    with Session(get_engine()) as db:
        for cluster in db.query(Cluster).all():
            dump = repr(cluster.__dict__)
            assert "top-secret-access-key" not in dump
            assert "top-secret-secret-key" not in dump
        for job in db.query(Job).all():
            dump = repr(job.__dict__)
            assert "top-secret-access-key" not in dump
            assert "top-secret-secret-key" not in dump


def test_delete_repository_blocked_by_snapshot_is_409(api_client, monkeypatch):
    from starrocks_br import repository as repository_module

    fake_db = FakeDB()
    _patch_connect(monkeypatch, fake_db)
    monkeypatch.setattr(repository_module, "has_snapshots", lambda db, name: True)

    cluster_id = _create_cluster(api_client)
    response = api_client.delete(f"/cluster/{cluster_id}/repositories/my_repo")

    assert response.status_code == 409


def test_delete_repository_succeeds_when_empty(api_client, monkeypatch):
    from starrocks_br import repository as repository_module

    fake_db = FakeDB()
    _patch_connect(monkeypatch, fake_db)
    monkeypatch.setattr(repository_module, "has_snapshots", lambda db, name: False)
    dropped = []
    monkeypatch.setattr(repository_module, "drop_repository", lambda db, name: dropped.append(name))

    cluster_id = _create_cluster(api_client)
    response = api_client.delete(f"/cluster/{cluster_id}/repositories/my_repo")

    assert response.status_code == 204
    assert dropped == ["my_repo"]


def test_delete_repository_unknown_cluster_is_404(api_client):
    response = api_client.delete("/cluster/999/repositories/my_repo")
    assert response.status_code == 404


def test_delete_repository_unknown_repository_is_404(api_client, monkeypatch):
    from starrocks_br import repository as repository_module

    fake_db = FakeDB()
    _patch_connect(monkeypatch, fake_db)

    def _raise_not_found(db, name):
        raise RepositoryNotFoundError(f"Repository '{name}' not found")

    monkeypatch.setattr(repository_module, "has_snapshots", _raise_not_found)

    cluster_id = _create_cluster(api_client)
    response = api_client.delete(f"/cluster/{cluster_id}/repositories/missing_repo")

    assert response.status_code == 404
