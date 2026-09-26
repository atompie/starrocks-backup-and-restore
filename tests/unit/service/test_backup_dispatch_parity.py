"""Proves manual and scheduled backup_full/backup_incremental submissions
converge on the same command with equivalent context, per
openspec/changes/unify-backup-command-entrypoints.

Both invocation sources end up calling `jobs.handlers.JOB_HANDLERS[job_type]`
(which is `commands.backup.run_backup_full`/`run_backup_incremental`, see
`test_jobs_handlers.py`) with a `params` dict built from the group id and
repository. These tests capture what actually reaches that handler for each
source and assert the group/repository/type-specific-option context is
equivalent, rather than merely asserting each source independently produces
*a* job.
"""

import datetime
import time

CLUSTER_PAYLOAD = {
    "name": "prod-eu",
    "host": "sr.internal",
    "port": 9030,
    "user": "backup_svc",
    "password": "s3cret",
}


def _create_cluster(api_client, name: str = "prod-eu") -> int:
    payload = {**CLUSTER_PAYLOAD, "name": name}
    return api_client.post("/cluster", json=payload).json()["id"]


def _create_group(api_client, cluster_id: int, name: str = "g1") -> int:
    response = api_client.post(
        f"/inventories/cluster/{cluster_id}",
        json={"name": name, "tables": [{"database": "sales_db", "table": "*"}]},
    )
    assert response.status_code == 201
    return response.json()["id"]


def _mock_group_check(monkeypatch):
    from starrocks_br import inventory_groups

    monkeypatch.setattr(inventory_groups, "group_exists", lambda db, cluster_id, group_id: True)


def _mock_manual_repository_check(monkeypatch):
    from starrocks_br.api.routes import jobs as jobs_module

    monkeypatch.setattr(jobs_module, "_ensure_repository_exists", lambda cluster, repository_name: None)


def _mock_schedule_repository_check(monkeypatch):
    from starrocks_br.api.routes import schedules as schedules_module

    monkeypatch.setattr(
        schedules_module, "ensure_repository_exists", lambda cluster, repository_name: None
    )


def _wait_for_terminal(api_client, job_id, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = api_client.get(f"/job/{job_id}").json()
        if body["status"] in ("SUCCESS", "FAILED"):
            return body
        time.sleep(0.02)
    raise TimeoutError("job did not finish in time")


def _capture_handler(calls: list[tuple], result=None):
    def _handler(cluster, params, on_progress=None):
        calls.append((cluster, dict(params)))
        return result or {}

    return _handler


def test_manual_and_scheduled_backup_full_invoke_same_command_with_equivalent_context(
    api_client, monkeypatch
):
    from starrocks_br.jobs import handlers
    from starrocks_br.store import session as session_module
    from starrocks_br.store.models import Schedule

    _mock_group_check(monkeypatch)
    _mock_manual_repository_check(monkeypatch)
    _mock_schedule_repository_check(monkeypatch)

    calls: list[tuple] = []
    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", _capture_handler(calls))

    cluster_id = _create_cluster(api_client)
    group_id = _create_group(api_client, cluster_id)

    manual_response = api_client.post(
        f"/backup/manual/full/cluster/{cluster_id}",
        json={"group_id": group_id, "repository": "s3_repo"},
    )
    assert manual_response.status_code == 202
    _wait_for_terminal(api_client, manual_response.json()["id"])

    created = api_client.post(
        f"/backup/schedules/cluster/{cluster_id}",
        json={
            "job_type": "backup_full",
            "inventory_group_id": group_id,
            "repository": "s3_repo",
            "cadence": "0 1 * * *",
        },
    ).json()
    forced_past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)
    with session_module.session_scope() as session:
        schedule = session.get(Schedule, created["id"])
        schedule.next_run_at = forced_past

    run_due_response = api_client.post("/backup/schedules/run")
    assert run_due_response.json()["triggered_count"] == 1
    _wait_for_terminal(api_client, run_due_response.json()["triggered_job_ids"][0])

    assert len(calls) == 2
    (manual_cluster, manual_params), (scheduled_cluster, scheduled_params) = calls

    assert manual_cluster.id == scheduled_cluster.id == cluster_id
    assert manual_params["group_id"] == scheduled_params["group_id"] == group_id
    assert manual_params["repository"] == scheduled_params["repository"] == "s3_repo"
    # The manual request carries the optional `name` override explicitly (None
    # when unset); a schedule never has one to offer. Both mean "no override".
    assert manual_params.get("name") is None
    assert scheduled_params.get("name") is None


def test_manual_and_scheduled_backup_incremental_invoke_same_command_with_equivalent_context(
    api_client, monkeypatch
):
    from starrocks_br.jobs import handlers
    from starrocks_br.store import session as session_module
    from starrocks_br.store.models import Schedule

    _mock_group_check(monkeypatch)
    _mock_manual_repository_check(monkeypatch)
    _mock_schedule_repository_check(monkeypatch)

    calls: list[tuple] = []
    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_incremental", _capture_handler(calls))

    cluster_id = _create_cluster(api_client)
    group_id = _create_group(api_client, cluster_id)

    manual_response = api_client.post(
        f"/backup/manual/incremental/cluster/{cluster_id}",
        json={"group_id": group_id, "repository": "s3_repo"},
    )
    assert manual_response.status_code == 202
    _wait_for_terminal(api_client, manual_response.json()["id"])

    created = api_client.post(
        f"/backup/schedules/cluster/{cluster_id}",
        json={
            "job_type": "backup_incremental",
            "inventory_group_id": group_id,
            "repository": "s3_repo",
            "cadence": "0 * * * *",
        },
    ).json()
    forced_past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)
    with session_module.session_scope() as session:
        schedule = session.get(Schedule, created["id"])
        schedule.next_run_at = forced_past

    run_due_response = api_client.post("/backup/schedules/run")
    assert run_due_response.json()["triggered_count"] == 1
    _wait_for_terminal(api_client, run_due_response.json()["triggered_job_ids"][0])

    assert len(calls) == 2
    (manual_cluster, manual_params), (scheduled_cluster, scheduled_params) = calls

    assert manual_cluster.id == scheduled_cluster.id == cluster_id
    assert manual_params["group_id"] == scheduled_params["group_id"] == group_id
    assert manual_params["repository"] == scheduled_params["repository"] == "s3_repo"
    # Neither source pins a baseline here; both mean "resolve the latest full
    # backup automatically" downstream in `run_backup_incremental`.
    assert manual_params.get("baseline_backup") is None
    assert scheduled_params.get("baseline_backup") is None
