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

    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", failing_handler)

    cluster_id = _create_cluster(api_client)
    submitted = api_client.post(f"/clusters/{cluster_id}/backups/full", json={"group": "g1"}).json()

    final = _wait_for_terminal(api_client, submitted["id"])

    assert final["status"] == "FAILED"
    assert final["error_message"] == "connection refused"


def test_backend_override_is_honored(api_client, monkeypatch):
    from starrocks_br.jobs import handlers

    monkeypatch.setitem(
        handlers.JOB_HANDLERS, "backup_full", lambda cluster, params, on_progress=None: {}
    )

    cluster_id = _create_cluster(api_client)
    response = api_client.post(
        f"/clusters/{cluster_id}/backups/full", json={"group": "g1", "backend": "thread"}
    )

    assert response.status_code == 202
    assert response.json()["backend"] == "thread"


def test_disabled_backend_is_rejected_with_422(api_client):
    cluster_id = _create_cluster(api_client)

    response = api_client.post(
        f"/clusters/{cluster_id}/backups/full", json={"group": "g1", "backend": "kafka"}
    )

    assert response.status_code == 422
