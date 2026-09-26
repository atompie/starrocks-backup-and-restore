import threading
import time

CLUSTER_PAYLOAD = {
    "name": "prod-eu",
    "host": "sr.internal",
    "port": 9030,
    "user": "backup_svc",
    "password": "s3cret",
}


def _create_cluster(api_client) -> int:
    return api_client.post("/cluster", json=CLUSTER_PAYLOAD).json()["id"]


def _mock_group_check(monkeypatch, exists=True):
    """Bypass the synchronous group-existence check for backup_full/incremental submission."""
    from starrocks_br import inventory_groups

    monkeypatch.setattr(
        inventory_groups, "group_exists", lambda db, cluster_id, group_id: exists
    )


def _mock_repository_check(monkeypatch):
    """Bypass the synchronous live repository-existence check for backup job submission."""
    from starrocks_br.api.routes import jobs as jobs_module

    monkeypatch.setattr(jobs_module, "_ensure_repository_exists", lambda cluster, repository_name: None)


def _wait_for_terminal(api_client, job_id, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = api_client.get(f"/job/{job_id}").json()
        if body["status"] in ("SUCCESS", "FAILED"):
            return body
        time.sleep(0.02)
    raise TimeoutError("job did not finish in time")


def test_submit_backup_full_returns_202_with_job(api_client, monkeypatch):
    from starrocks_br.jobs import handlers

    _mock_group_check(monkeypatch)
    _mock_repository_check(monkeypatch)
    monkeypatch.setitem(
        handlers.JOB_HANDLERS, "backup_full", lambda cluster, params, on_progress=None: {"label": "x"}
    )

    cluster_id = _create_cluster(api_client)
    response = api_client.post(
        f"/backup/manual/full/cluster/{cluster_id}", json={"group_id": 1, "repository": "s3_repo"}
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["cluster_id"] == cluster_id
    assert body["job_type"] == "backup_full"


def test_submit_job_against_unknown_cluster_is_404(api_client):
    response = api_client.post(
        "/backup/manual/full/cluster/999", json={"group_id": 1, "repository": "s3_repo"}
    )
    assert response.status_code == 404


def test_get_unknown_job_is_404(api_client):
    assert api_client.get("/job/999").status_code == 404


def test_submit_then_poll_to_terminal_state_success(api_client, monkeypatch):
    from starrocks_br.jobs import handlers

    def handler(cluster, params, on_progress=None):
        return {"label": "done"}

    _mock_group_check(monkeypatch)
    _mock_repository_check(monkeypatch)
    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", handler)

    cluster_id = _create_cluster(api_client)
    submitted = api_client.post(
        f"/backup/manual/full/cluster/{cluster_id}", json={"group_id": 1, "repository": "s3_repo"}
    ).json()

    final = _wait_for_terminal(api_client, submitted["id"])

    assert final["status"] == "SUCCESS"
    assert final["started_at"] is not None
    assert final["finished_at"] is not None


def test_poll_reports_progress_mid_run_then_terminal(api_client, monkeypatch):
    from starrocks_br.jobs import handlers

    release = threading.Event()

    def handler(cluster, params, on_progress=None):
        on_progress({"state": "UPLOADING", "progress_pct": 55})
        release.wait(timeout=2)
        return {}

    _mock_group_check(monkeypatch)
    _mock_repository_check(monkeypatch)
    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", handler)

    cluster_id = _create_cluster(api_client)
    submitted = api_client.post(
        f"/backup/manual/full/cluster/{cluster_id}", json={"group_id": 1, "repository": "s3_repo"}
    ).json()

    deadline = time.time() + 2
    seen_progress = None
    while time.time() < deadline:
        body = api_client.get(f"/job/{submitted['id']}").json()
        if body["progress_pct"] is not None:
            seen_progress = body
            break
        time.sleep(0.02)

    release.set()
    final = _wait_for_terminal(api_client, submitted["id"])

    assert seen_progress is not None
    assert seen_progress["progress_pct"] == 55
    assert seen_progress["state_detail"] == "UPLOADING"
    assert final["status"] == "SUCCESS"


def test_no_progress_phase_reports_running_without_percentage(api_client, monkeypatch):
    from starrocks_br.jobs import handlers

    release = threading.Event()

    def handler(cluster, params, on_progress=None):
        on_progress({"state": "PENDING", "progress_pct": None})
        release.wait(timeout=2)
        return {}

    _mock_group_check(monkeypatch)
    _mock_repository_check(monkeypatch)
    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", handler)

    cluster_id = _create_cluster(api_client)
    submitted = api_client.post(
        f"/backup/manual/full/cluster/{cluster_id}", json={"group_id": 1, "repository": "s3_repo"}
    ).json()

    deadline = time.time() + 2
    seen_running = None
    while time.time() < deadline:
        body = api_client.get(f"/job/{submitted['id']}").json()
        if body["status"] == "RUNNING":
            seen_running = body
            break
        time.sleep(0.02)

    release.set()
    _wait_for_terminal(api_client, submitted["id"])

    assert seen_running is not None
    assert seen_running["progress_pct"] is None
    assert seen_running["started_at"] is not None


def test_submit_job_failure_is_reported(api_client, monkeypatch):
    from starrocks_br.jobs import handlers

    def failing_handler(cluster, params, on_progress=None):
        raise RuntimeError("connection refused")

    _mock_group_check(monkeypatch)
    _mock_repository_check(monkeypatch)
    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", failing_handler)

    cluster_id = _create_cluster(api_client)
    submitted = api_client.post(
        f"/backup/manual/full/cluster/{cluster_id}", json={"group_id": 1, "repository": "s3_repo"}
    ).json()

    final = _wait_for_terminal(api_client, submitted["id"])

    assert final["status"] == "FAILED"
    assert final["error_message"] == "connection refused"


def test_backend_override_is_honored(api_client, monkeypatch):
    from starrocks_br.jobs import handlers

    _mock_group_check(monkeypatch)
    _mock_repository_check(monkeypatch)
    monkeypatch.setitem(
        handlers.JOB_HANDLERS, "backup_full", lambda cluster, params, on_progress=None: {}
    )

    cluster_id = _create_cluster(api_client)
    response = api_client.post(
        f"/backup/manual/full/cluster/{cluster_id}",
        json={"group_id": 1, "repository": "s3_repo", "backend": "thread"},
    )

    assert response.status_code == 202
    assert response.json()["backend"] == "thread"


def test_unrecognized_backend_value_is_rejected_with_422(api_client, monkeypatch):
    """A backend value outside {"thread", "job"} is rejected at the schema layer,
    before any group/repository/enabled-backend check runs."""
    cluster_id = _create_cluster(api_client)

    response = api_client.post(
        f"/backup/manual/full/cluster/{cluster_id}",
        json={"group_id": 1, "repository": "s3_repo", "backend": "kafka"},
    )

    assert response.status_code == 422


def test_recognized_but_disabled_backend_is_rejected_with_422(api_client, monkeypatch):
    """"job" is a recognized backend identifier but not enabled in this test server
    (STARROCKS_BR_ENABLED_BACKENDS=thread) - it must be rejected by the enabled-
    backend check in jobs.py, distinct from the schema-level enum check above."""
    _mock_group_check(monkeypatch)
    _mock_repository_check(monkeypatch)
    cluster_id = _create_cluster(api_client)

    response = api_client.post(
        f"/backup/manual/full/cluster/{cluster_id}",
        json={"group_id": 1, "repository": "s3_repo", "backend": "job"},
    )

    assert response.status_code == 422


def test_submit_backup_full_unknown_group_is_404(api_client, monkeypatch):
    _mock_group_check(monkeypatch, exists=False)
    cluster_id = _create_cluster(api_client)

    response = api_client.post(
        f"/backup/manual/full/cluster/{cluster_id}", json={"group_id": 999, "repository": "s3_repo"}
    )

    assert response.status_code == 404


def test_submit_backup_incremental_unknown_group_is_404(api_client, monkeypatch):
    _mock_group_check(monkeypatch, exists=False)
    cluster_id = _create_cluster(api_client)

    response = api_client.post(
        f"/backup/manual/incremental/cluster/{cluster_id}",
        json={"group_id": 999, "repository": "s3_repo"},
    )

    assert response.status_code == 404


def test_submit_backup_full_missing_group_is_422(api_client, monkeypatch):
    _mock_group_check(monkeypatch, exists=False)
    cluster_id = _create_cluster(api_client)

    response = api_client.post(
        f"/backup/manual/full/cluster/{cluster_id}", json={"repository": "s3_repo"}
    )

    assert response.status_code == 422


def test_submit_backup_full_missing_repository_is_422(api_client, monkeypatch):
    _mock_group_check(monkeypatch)
    cluster_id = _create_cluster(api_client)

    response = api_client.post(f"/backup/manual/full/cluster/{cluster_id}", json={"group_id": 1})

    assert response.status_code == 422


def test_submit_backup_full_unknown_repository_is_404(api_client, monkeypatch):
    from starrocks_br.api.routes import _cluster_connect

    _mock_group_check(monkeypatch)
    monkeypatch.setattr(
        _cluster_connect, "connect_or_503", lambda cluster: type(
            "FakeDB", (), {"close": lambda self: None}
        )()
    )
    monkeypatch.setattr(_cluster_connect.repository_module, "list_repositories", lambda db: [])
    cluster_id = _create_cluster(api_client)

    response = api_client.post(
        f"/backup/manual/full/cluster/{cluster_id}", json={"group_id": 1, "repository": "missing_repo"}
    )

    assert response.status_code == 404


def test_submit_backup_full_rejects_foreign_field_is_422(api_client, monkeypatch):
    _mock_group_check(monkeypatch)
    _mock_repository_check(monkeypatch)
    cluster_id = _create_cluster(api_client)

    response = api_client.post(
        f"/backup/manual/full/cluster/{cluster_id}",
        json={"group_id": 1, "repository": "s3_repo", "keep_last": 5},
    )

    assert response.status_code == 422


def test_submit_restore_both_group_and_table_is_422(api_client):
    cluster_id = _create_cluster(api_client)

    response = api_client.post(
        f"/backup/manual/restore/cluster/{cluster_id}",
        json={"target_label": "x", "group_id": 1, "table": "t1"},
    )

    assert response.status_code == 422


def test_submit_restore_neither_group_nor_table_succeeds(api_client, monkeypatch):
    from starrocks_br.jobs import handlers

    monkeypatch.setitem(
        handlers.JOB_HANDLERS, "restore", lambda cluster, params, on_progress=None: {}
    )

    cluster_id = _create_cluster(api_client)
    response = api_client.post(
        f"/backup/manual/restore/cluster/{cluster_id}", json={"target_label": "x"}
    )

    assert response.status_code == 202


def test_submit_prune_no_strategy_is_422(api_client):
    cluster_id = _create_cluster(api_client)

    response = api_client.post(f"/backup/manual/prune/cluster/{cluster_id}", json={})

    assert response.status_code == 422


def test_submit_prune_two_strategies_is_422(api_client):
    cluster_id = _create_cluster(api_client)

    response = api_client.post(
        f"/backup/manual/prune/cluster/{cluster_id}", json={"keep_last": 3, "older_than": "7d"}
    )

    assert response.status_code == 422


def test_submit_prune_extra_field_is_422(api_client):
    cluster_id = _create_cluster(api_client)

    response = api_client.post(
        f"/backup/manual/prune/cluster/{cluster_id}", json={"snapshot": "x", "table": "t"}
    )

    assert response.status_code == 422
