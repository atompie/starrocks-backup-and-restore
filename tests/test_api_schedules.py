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

import datetime
import threading

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


def test_create_schedule_computes_next_run_at(api_client):
    cluster_id = _create_cluster(api_client)

    response = api_client.post(
        "/schedule",
        json={
            "cluster_id": cluster_id,
            "job_type": "backup_full",
            "group_name": "g1",
            "cadence": "0 1 * * *",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["next_run_at"] is not None
    assert body["enabled"] is True


def test_create_schedule_unknown_cluster_404(api_client):
    response = api_client.post(
        "/schedule",
        json={"cluster_id": 999, "job_type": "backup_full", "group_name": "g1", "cadence": "0 1 * * *"},
    )
    assert response.status_code == 404


def test_create_schedule_invalid_cadence_422(api_client):
    cluster_id = _create_cluster(api_client)

    response = api_client.post(
        "/schedule",
        json={
            "cluster_id": cluster_id,
            "job_type": "backup_full",
            "group_name": "g1",
            "cadence": "not a cron expression",
        },
    )
    assert response.status_code == 422


def test_disable_schedule_excludes_it_from_run_due(api_client, monkeypatch):
    from starrocks_br.jobs import handlers

    monkeypatch.setitem(
        handlers.JOB_HANDLERS, "backup_full", lambda cluster, params, on_progress=None: {}
    )

    cluster_id = _create_cluster(api_client)
    created = api_client.post(
        "/schedule",
        json={
            "cluster_id": cluster_id,
            "job_type": "backup_full",
            "group_name": "g1",
            "cadence": "* * * * *",
        },
    ).json()

    api_client.patch(f"/schedule/{created['id']}", json={"enabled": False})

    response = api_client.post("/schedules/run-due")

    assert response.json()["triggered_count"] == 0


def test_delete_schedule_removes_it(api_client):
    cluster_id = _create_cluster(api_client)
    created = api_client.post(
        "/schedule",
        json={
            "cluster_id": cluster_id,
            "job_type": "backup_full",
            "group_name": "g1",
            "cadence": "0 1 * * *",
        },
    ).json()

    response = api_client.delete(f"/schedule/{created['id']}")

    assert response.status_code == 204
    assert api_client.get(f"/schedule/{created['id']}").status_code == 404


def test_run_due_triggers_a_due_schedule(api_client, monkeypatch):
    from starrocks_br.jobs import handlers
    from starrocks_br.store import session as session_module
    from starrocks_br.store.models import Schedule

    monkeypatch.setitem(
        handlers.JOB_HANDLERS, "backup_full", lambda cluster, params, on_progress=None: {}
    )

    cluster_id = _create_cluster(api_client)
    created = api_client.post(
        "/schedule",
        json={
            "cluster_id": cluster_id,
            "job_type": "backup_full",
            "group_name": "g1",
            "cadence": "0 1 * * *",
        },
    ).json()

    # Force it due: set next_run_at into the past directly in the store.
    forced_past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)
    with session_module.session_scope() as session:
        schedule = session.get(Schedule, created["id"])
        schedule.next_run_at = forced_past

    response = api_client.post("/schedules/run-due")

    assert response.status_code == 200
    body = response.json()
    assert body["triggered_count"] == 1
    assert len(body["triggered_job_ids"]) == 1

    updated = api_client.get(f"/schedule/{created['id']}").json()
    assert updated["last_run_job_id"] == body["triggered_job_ids"][0]
    assert updated["next_run_at"] > forced_past.isoformat()


def test_run_due_skips_not_yet_due_schedule(api_client):
    cluster_id = _create_cluster(api_client)
    api_client.post(
        "/schedule",
        json={
            "cluster_id": cluster_id,
            "job_type": "backup_full",
            "group_name": "g1",
            "cadence": "0 1 1 1 *",  # once a year - far in the future
        },
    )

    response = api_client.post("/schedules/run-due")

    assert response.json()["triggered_count"] == 0


def test_run_due_with_no_schedules_is_a_no_op(api_client):
    response = api_client.post("/schedules/run-due")

    assert response.status_code == 200
    assert response.json() == {"triggered_job_ids": [], "triggered_count": 0}


def test_run_due_is_idempotent_under_concurrent_calls(api_client, monkeypatch):
    from starrocks_br.jobs import handlers
    from starrocks_br.store import session as session_module
    from starrocks_br.store.models import Schedule

    monkeypatch.setitem(
        handlers.JOB_HANDLERS, "backup_full", lambda cluster, params, on_progress=None: {}
    )

    cluster_id = _create_cluster(api_client)
    created = api_client.post(
        "/schedule",
        json={
            "cluster_id": cluster_id,
            "job_type": "backup_full",
            "group_name": "g1",
            "cadence": "0 1 * * *",
        },
    ).json()

    with session_module.session_scope() as session:
        schedule = session.get(Schedule, created["id"])
        schedule.next_run_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            minutes=1
        )

    results = []

    def call_run_due():
        results.append(api_client.post("/schedules/run-due").json())

    threads = [threading.Thread(target=call_run_due) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total_triggered = sum(r["triggered_count"] for r in results)
    assert total_triggered == 1
