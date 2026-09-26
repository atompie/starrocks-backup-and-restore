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


def _create_cluster(api_client, name: str = "prod-eu") -> int:
    payload = {**CLUSTER_PAYLOAD, "name": name}
    return api_client.post("/cluster", json=payload).json()["id"]


def _create_group(api_client, cluster_id, name="g1") -> int:
    response = api_client.post(
        f"/cluster/{cluster_id}/inventory-groups",
        json={"name": name, "tables": [{"database": "sales_db", "table": "*"}]},
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_create_schedule_computes_next_run_at(api_client):
    cluster_id = _create_cluster(api_client)
    group_id = _create_group(api_client, cluster_id)

    response = api_client.post(
        f"/cluster/{cluster_id}/schedules",
        json={
            "job_type": "backup_full",
            "inventory_group_id": group_id,
            "cadence": "0 1 * * *",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["cluster_id"] == cluster_id
    assert body["inventory_group_id"] == group_id
    assert body["next_run_at"] is not None
    assert body["enabled"] is True


def test_create_schedule_unknown_cluster_404(api_client):
    response = api_client.post(
        "/cluster/999/schedules",
        json={"job_type": "backup_full", "inventory_group_id": 1, "cadence": "0 1 * * *"},
    )
    assert response.status_code == 404


def test_create_schedule_unknown_group_404(api_client):
    cluster_id = _create_cluster(api_client)

    response = api_client.post(
        f"/cluster/{cluster_id}/schedules",
        json={"job_type": "backup_full", "inventory_group_id": 999, "cadence": "0 1 * * *"},
    )
    assert response.status_code == 404


def test_create_schedule_invalid_cadence_422(api_client):
    cluster_id = _create_cluster(api_client)
    group_id = _create_group(api_client, cluster_id)

    response = api_client.post(
        f"/cluster/{cluster_id}/schedules",
        json={
            "job_type": "backup_full",
            "inventory_group_id": group_id,
            "cadence": "not a cron expression",
        },
    )
    assert response.status_code == 422


def test_list_schedules_scoped_to_cluster(api_client):
    cluster_a = _create_cluster(api_client, "cluster-a")
    cluster_b = _create_cluster(api_client, "cluster-b")
    group_a = _create_group(api_client, cluster_a, "g1")
    group_b = _create_group(api_client, cluster_b, "g2")

    api_client.post(
        f"/cluster/{cluster_a}/schedules",
        json={"job_type": "backup_full", "inventory_group_id": group_a, "cadence": "0 1 * * *"},
    )
    api_client.post(
        f"/cluster/{cluster_b}/schedules",
        json={"job_type": "backup_full", "inventory_group_id": group_b, "cadence": "0 1 * * *"},
    )

    response = api_client.get(f"/cluster/{cluster_a}/schedules")

    assert response.status_code == 200
    schedules = response.json()
    assert len(schedules) == 1
    assert schedules[0]["inventory_group_id"] == group_a
    assert schedules[0]["cluster_id"] == cluster_a


def test_list_schedules_unknown_cluster_404(api_client):
    response = api_client.get("/cluster/999/schedules")
    assert response.status_code == 404


def test_get_update_delete_schedule_via_wrong_cluster_404(api_client):
    cluster_a = _create_cluster(api_client, "cluster-a")
    cluster_b = _create_cluster(api_client, "cluster-b")
    group_a = _create_group(api_client, cluster_a)

    created = api_client.post(
        f"/cluster/{cluster_a}/schedules",
        json={"job_type": "backup_full", "inventory_group_id": group_a, "cadence": "0 1 * * *"},
    ).json()

    assert api_client.get(f"/cluster/{cluster_b}/schedule/{created['id']}").status_code == 404
    assert (
        api_client.patch(
            f"/cluster/{cluster_b}/schedule/{created['id']}", json={"enabled": False}
        ).status_code
        == 404
    )
    assert api_client.delete(f"/cluster/{cluster_b}/schedule/{created['id']}").status_code == 404

    # Still reachable, untouched, via its own cluster.
    assert api_client.get(f"/cluster/{cluster_a}/schedule/{created['id']}").status_code == 200


def test_update_schedule_unknown_group_404(api_client):
    cluster_id = _create_cluster(api_client)
    group_id = _create_group(api_client, cluster_id)
    created = api_client.post(
        f"/cluster/{cluster_id}/schedules",
        json={"job_type": "backup_full", "inventory_group_id": group_id, "cadence": "0 1 * * *"},
    ).json()

    response = api_client.patch(
        f"/cluster/{cluster_id}/schedule/{created['id']}", json={"inventory_group_id": 999}
    )

    assert response.status_code == 404


def test_disable_schedule_excludes_it_from_run_due(api_client, monkeypatch):
    from starrocks_br.jobs import handlers

    monkeypatch.setitem(
        handlers.JOB_HANDLERS, "backup_full", lambda cluster, params, on_progress=None: {}
    )

    cluster_id = _create_cluster(api_client)
    group_id = _create_group(api_client, cluster_id)
    created = api_client.post(
        f"/cluster/{cluster_id}/schedules",
        json={
            "job_type": "backup_full",
            "inventory_group_id": group_id,
            "cadence": "* * * * *",
        },
    ).json()

    api_client.patch(f"/cluster/{cluster_id}/schedule/{created['id']}", json={"enabled": False})

    response = api_client.post("/schedules/run-due")

    assert response.json()["triggered_count"] == 0


def test_delete_schedule_removes_it(api_client):
    cluster_id = _create_cluster(api_client)
    group_id = _create_group(api_client, cluster_id)
    created = api_client.post(
        f"/cluster/{cluster_id}/schedules",
        json={
            "job_type": "backup_full",
            "inventory_group_id": group_id,
            "cadence": "0 1 * * *",
        },
    ).json()

    response = api_client.delete(f"/cluster/{cluster_id}/schedule/{created['id']}")

    assert response.status_code == 204
    assert api_client.get(f"/cluster/{cluster_id}/schedule/{created['id']}").status_code == 404


def test_run_due_triggers_a_due_schedule(api_client, monkeypatch):
    from starrocks_br.jobs import handlers
    from starrocks_br.store import session as session_module
    from starrocks_br.store.models import Schedule

    monkeypatch.setitem(
        handlers.JOB_HANDLERS, "backup_full", lambda cluster, params, on_progress=None: {}
    )

    cluster_id = _create_cluster(api_client)
    group_id = _create_group(api_client, cluster_id)
    created = api_client.post(
        f"/cluster/{cluster_id}/schedules",
        json={
            "job_type": "backup_full",
            "inventory_group_id": group_id,
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

    updated = api_client.get(f"/cluster/{cluster_id}/schedule/{created['id']}").json()
    assert updated["last_run_job_id"] == body["triggered_job_ids"][0]
    assert updated["next_run_at"] > forced_past.isoformat()


def test_run_due_skips_not_yet_due_schedule(api_client):
    cluster_id = _create_cluster(api_client)
    group_id = _create_group(api_client, cluster_id)
    api_client.post(
        f"/cluster/{cluster_id}/schedules",
        json={
            "job_type": "backup_full",
            "inventory_group_id": group_id,
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
    group_id = _create_group(api_client, cluster_id)
    created = api_client.post(
        f"/cluster/{cluster_id}/schedules",
        json={
            "job_type": "backup_full",
            "inventory_group_id": group_id,
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
