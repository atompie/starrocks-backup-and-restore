import threading
import time

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
    return api_client.post("/clusters", json=CLUSTER_PAYLOAD).json()["id"]


class _FakeDB:
    def close(self):
        pass


def _mock_group_check(monkeypatch, exists=True):
    """Bypass the synchronous group-existence check for backup_full/incremental submission."""
    from starrocks_br import inventory_groups
    from starrocks_br.api.routes import jobs as jobs_module

    monkeypatch.setattr(jobs_module, "connect_or_503", lambda cluster: _FakeDB())
    monkeypatch.setattr(
        inventory_groups, "group_exists", lambda db, group, ops_database: exists
    )


def _wait_for_terminal(api_client, job_id, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = api_client.get(f"/jobs/{job_id}").json()
        if body["status"] in ("SUCCESS", "FAILED"):
            return body
        time.sleep(0.02)
    raise TimeoutError("job did not finish in time")


def test_submit_backup_full_returns_202_with_job(api_client, monkeypatch):
    from starrocks_br.jobs import handlers

    _mock_group_check(monkeypatch)
    monkeypatch.setitem(
        handlers.JOB_HANDLERS, "backup_full", lambda cluster, params, on_progress=None: {"label": "x"}
    )

    cluster_id = _create_cluster(api_client)
    response = api_client.post(f"/clusters/{cluster_id}/backups/full", json={"group": "g1"})

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["cluster_id"] == cluster_id
    assert body["job_type"] == "backup_full"


def test_submit_job_against_unknown_cluster_is_404(api_client):
    response = api_client.post("/clusters/999/backups/full", json={"group": "g1"})
    assert response.status_code == 404


def test_get_unknown_job_is_404(api_client):
    assert api_client.get("/jobs/999").status_code == 404


def test_submit_then_poll_to_terminal_state_success(api_client, monkeypatch):
    from starrocks_br.jobs import handlers

    def handler(cluster, params, on_progress=None):
        return {"label": "done"}

    _mock_group_check(monkeypatch)
    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", handler)

    cluster_id = _create_cluster(api_client)
    submitted = api_client.post(f"/clusters/{cluster_id}/backups/full", json={"group": "g1"}).json()

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
    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", handler)

    cluster_id = _create_cluster(api_client)
    submitted = api_client.post(f"/clusters/{cluster_id}/backups/full", json={"group": "g1"}).json()

    deadline = time.time() + 2
    seen_progress = None
    while time.time() < deadline:
        body = api_client.get(f"/jobs/{submitted['id']}").json()
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
    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", handler)

    cluster_id = _create_cluster(api_client)
    submitted = api_client.post(f"/clusters/{cluster_id}/backups/full", json={"group": "g1"}).json()

    deadline = time.time() + 2
    seen_running = None
    while time.time() < deadline:
        body = api_client.get(f"/jobs/{submitted['id']}").json()
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
    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", failing_handler)

    cluster_id = _create_cluster(api_client)
    submitted = api_client.post(f"/clusters/{cluster_id}/backups/full", json={"group": "g1"}).json()

    final = _wait_for_terminal(api_client, submitted["id"])

    assert final["status"] == "FAILED"
    assert final["error_message"] == "connection refused"


def test_backend_override_is_honored(api_client, monkeypatch):
    from starrocks_br.jobs import handlers

    _mock_group_check(monkeypatch)
    monkeypatch.setitem(
        handlers.JOB_HANDLERS, "backup_full", lambda cluster, params, on_progress=None: {}
    )

    cluster_id = _create_cluster(api_client)
    response = api_client.post(
        f"/clusters/{cluster_id}/backups/full", json={"group": "g1", "backend": "thread"}
    )

    assert response.status_code == 202
    assert response.json()["backend"] == "thread"


def test_disabled_backend_is_rejected_with_422(api_client, monkeypatch):
    _mock_group_check(monkeypatch)
    cluster_id = _create_cluster(api_client)

    response = api_client.post(
        f"/clusters/{cluster_id}/backups/full", json={"group": "g1", "backend": "kafka"}
    )

    assert response.status_code == 422


def test_submit_backup_full_unknown_group_is_404(api_client, monkeypatch):
    _mock_group_check(monkeypatch, exists=False)
    cluster_id = _create_cluster(api_client)

    response = api_client.post(f"/clusters/{cluster_id}/backups/full", json={"group": "no_such_group"})

    assert response.status_code == 404


def test_submit_backup_incremental_unknown_group_is_404(api_client, monkeypatch):
    _mock_group_check(monkeypatch, exists=False)
    cluster_id = _create_cluster(api_client)

    response = api_client.post(
        f"/clusters/{cluster_id}/backups/incremental", json={"group": "no_such_group"}
    )

    assert response.status_code == 404


def test_submit_backup_full_missing_group_is_422(api_client, monkeypatch):
    _mock_group_check(monkeypatch, exists=False)
    cluster_id = _create_cluster(api_client)

    response = api_client.post(f"/clusters/{cluster_id}/backups/full", json={})

    assert response.status_code == 422


def test_submit_backup_full_rejects_foreign_field_is_422(api_client, monkeypatch):
    _mock_group_check(monkeypatch)
    cluster_id = _create_cluster(api_client)

    response = api_client.post(
        f"/clusters/{cluster_id}/backups/full", json={"group": "g1", "keep_last": 5}
    )

    assert response.status_code == 422


def test_submit_restore_both_group_and_table_is_422(api_client):
    cluster_id = _create_cluster(api_client)

    response = api_client.post(
        f"/clusters/{cluster_id}/restores",
        json={"target_label": "x", "group": "g1", "table": "t1"},
    )

    assert response.status_code == 422


def test_submit_restore_neither_group_nor_table_succeeds(api_client, monkeypatch):
    from starrocks_br.jobs import handlers

    monkeypatch.setitem(
        handlers.JOB_HANDLERS, "restore", lambda cluster, params, on_progress=None: {}
    )

    cluster_id = _create_cluster(api_client)
    response = api_client.post(
        f"/clusters/{cluster_id}/restores", json={"target_label": "x"}
    )

    assert response.status_code == 202


def test_submit_prune_no_strategy_is_422(api_client):
    cluster_id = _create_cluster(api_client)

    response = api_client.post(f"/clusters/{cluster_id}/prunes", json={})

    assert response.status_code == 422


def test_submit_prune_two_strategies_is_422(api_client):
    cluster_id = _create_cluster(api_client)

    response = api_client.post(
        f"/clusters/{cluster_id}/prunes", json={"keep_last": 3, "older_than": "7d"}
    )

    assert response.status_code == 422


def test_submit_prune_extra_field_is_422(api_client):
    cluster_id = _create_cluster(api_client)

    response = api_client.post(
        f"/clusters/{cluster_id}/prunes", json={"snapshot": "x", "table": "t"}
    )

    assert response.status_code == 422
