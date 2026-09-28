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
    from starrocks_br.dal.metadata import inventory_groups

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
        handlers.JOB_HANDLERS, "backup_full", lambda cluster, params, job_id, on_progress=None: {"label": "x"}
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

    def handler(cluster, params, job_id, on_progress=None):
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
    assert final["schedule_id"] is None
    assert final["group_id"] == 1
    assert final["baseline_job_id"] is None
    assert final["result_json"] is not None


def test_poll_reports_progress_mid_run_then_terminal(api_client, monkeypatch):
    from starrocks_br.jobs import handlers

    release = threading.Event()

    def handler(cluster, params, job_id, on_progress=None):
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

    def handler(cluster, params, job_id, on_progress=None):
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

    def failing_handler(cluster, params, job_id, on_progress=None):
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
        handlers.JOB_HANDLERS, "backup_full", lambda cluster, params, job_id, on_progress=None: {}
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
        handlers.JOB_HANDLERS, "restore", lambda cluster, params, job_id, on_progress=None: {}
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


def _submit_backup_full(api_client, monkeypatch, cluster_id, group_id=1):
    from starrocks_br.jobs import handlers

    _mock_group_check(monkeypatch)
    _mock_repository_check(monkeypatch)
    monkeypatch.setitem(
        handlers.JOB_HANDLERS, "backup_full", lambda cluster, params, job_id, on_progress=None: {}
    )
    submitted = api_client.post(
        f"/backup/manual/full/cluster/{cluster_id}",
        json={"group_id": group_id, "repository": "s3_repo"},
    ).json()
    return _wait_for_terminal(api_client, submitted["id"])


def _submit_prune(api_client, monkeypatch, cluster_id, group_id=1):
    from starrocks_br.jobs import handlers

    _mock_group_check(monkeypatch)
    monkeypatch.setitem(
        handlers.JOB_HANDLERS, "prune", lambda cluster, params, job_id, on_progress=None: {}
    )
    submitted = api_client.post(
        f"/backup/manual/prune/cluster/{cluster_id}",
        json={"group_id": group_id, "keep_last": 3},
    ).json()
    return _wait_for_terminal(api_client, submitted["id"])


def test_backup_history_empty_for_cluster_with_no_jobs(api_client):
    cluster_id = _create_cluster(api_client)

    response = api_client.get(f"/backup/history/cluster/{cluster_id}")

    assert response.status_code == 200
    assert response.json() == []


def test_backup_history_unknown_cluster_is_404(api_client):
    response = api_client.get("/backup/history/cluster/999")

    assert response.status_code == 404


def test_backup_history_defaults_to_backup_job_types(api_client, monkeypatch):
    cluster_id = _create_cluster(api_client)
    backup_job = _submit_backup_full(api_client, monkeypatch, cluster_id)
    _submit_prune(api_client, monkeypatch, cluster_id)

    response = api_client.get(f"/backup/history/cluster/{cluster_id}")

    assert response.status_code == 200
    body = response.json()
    assert [job["id"] for job in body] == [backup_job["id"]]


def test_backup_history_filters_by_job_type(api_client, monkeypatch):
    cluster_id = _create_cluster(api_client)
    _submit_backup_full(api_client, monkeypatch, cluster_id)
    prune_job = _submit_prune(api_client, monkeypatch, cluster_id)

    response = api_client.get(f"/backup/history/cluster/{cluster_id}", params={"job_type": "prune"})

    assert response.status_code == 200
    body = response.json()
    assert [job["id"] for job in body] == [prune_job["id"]]


def test_backup_history_filters_by_status(api_client, monkeypatch):
    from starrocks_br.jobs import handlers

    cluster_id = _create_cluster(api_client)
    _mock_group_check(monkeypatch)
    _mock_repository_check(monkeypatch)

    monkeypatch.setitem(
        handlers.JOB_HANDLERS,
        "backup_full",
        lambda cluster, params, job_id, on_progress=None: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    failed = api_client.post(
        f"/backup/manual/full/cluster/{cluster_id}", json={"group_id": 1, "repository": "s3_repo"}
    ).json()
    _wait_for_terminal(api_client, failed["id"])

    monkeypatch.setitem(
        handlers.JOB_HANDLERS, "backup_full", lambda cluster, params, job_id, on_progress=None: {}
    )
    succeeded = api_client.post(
        f"/backup/manual/full/cluster/{cluster_id}", json={"group_id": 1, "repository": "s3_repo"}
    ).json()
    _wait_for_terminal(api_client, succeeded["id"])

    response = api_client.get(f"/backup/history/cluster/{cluster_id}", params={"status": "FAILED"})

    assert response.status_code == 200
    body = response.json()
    assert [job["id"] for job in body] == [failed["id"]]


def test_backup_history_orders_most_recent_first_and_paginates(api_client, monkeypatch):
    cluster_id = _create_cluster(api_client)
    jobs = [_submit_backup_full(api_client, monkeypatch, cluster_id) for _ in range(3)]
    expected_order = list(reversed(jobs))

    response = api_client.get(
        f"/backup/history/cluster/{cluster_id}", params={"limit": 2, "offset": 1}
    )

    assert response.status_code == 200
    body = response.json()
    assert [job["id"] for job in body] == [expected_order[1]["id"], expected_order[2]["id"]]


def test_backup_history_filters_by_job_id(api_client, monkeypatch):
    cluster_id = _create_cluster(api_client)
    first = _submit_backup_full(api_client, monkeypatch, cluster_id)
    _submit_backup_full(api_client, monkeypatch, cluster_id)

    response = api_client.get(
        f"/backup/history/cluster/{cluster_id}", params={"job_id": first["id"]}
    )

    assert response.status_code == 200
    body = response.json()
    assert [job["id"] for job in body] == [first["id"]]


def test_backup_history_filters_by_job_id_with_no_match(api_client, monkeypatch):
    cluster_id = _create_cluster(api_client)
    _submit_backup_full(api_client, monkeypatch, cluster_id)

    response = api_client.get(f"/backup/history/cluster/{cluster_id}", params={"job_id": 999999})

    assert response.status_code == 200
    assert response.json() == []


def test_backup_history_filters_by_group_id(api_client, monkeypatch):
    cluster_id = _create_cluster(api_client)
    group_1_job = _submit_backup_full(api_client, monkeypatch, cluster_id, group_id=1)
    _submit_backup_full(api_client, monkeypatch, cluster_id, group_id=2)

    response = api_client.get(f"/backup/history/cluster/{cluster_id}", params={"group_id": 1})

    assert response.status_code == 200
    body = response.json()
    assert [job["id"] for job in body] == [group_1_job["id"]]


def test_backup_history_filters_by_group_id_with_no_match(api_client, monkeypatch):
    cluster_id = _create_cluster(api_client)
    _submit_backup_full(api_client, monkeypatch, cluster_id, group_id=1)

    response = api_client.get(f"/backup/history/cluster/{cluster_id}", params={"group_id": 999999})

    assert response.status_code == 200
    assert response.json() == []


def test_backup_history_filters_by_group_id_and_job_type_combined(api_client, monkeypatch):
    cluster_id = _create_cluster(api_client)
    backup_job = _submit_backup_full(api_client, monkeypatch, cluster_id, group_id=1)
    _submit_prune(api_client, monkeypatch, cluster_id, group_id=1)

    response = api_client.get(
        f"/backup/history/cluster/{cluster_id}",
        params={"group_id": 1, "job_type": "backup_full"},
    )

    assert response.status_code == 200
    body = response.json()
    assert [job["id"] for job in body] == [backup_job["id"]]


def _create_group(api_client, cluster_id, name="g1") -> int:
    response = api_client.post(
        f"/inventories/cluster/{cluster_id}",
        json={"name": name, "tables": [{"database": "sales_db", "table": "*"}]},
    )
    assert response.status_code == 201
    return response.json()["id"]


def _submit_via_one_shot_schedule(api_client, monkeypatch, cluster_id, group_id) -> dict:
    from starrocks_br.api.routes import schedules as schedules_module
    from starrocks_br.jobs import handlers

    monkeypatch.setattr(
        schedules_module, "ensure_repository_exists", lambda cluster, repository_name: None
    )
    monkeypatch.setitem(
        handlers.JOB_HANDLERS, "backup_full", lambda cluster, params, job_id, on_progress=None: {}
    )
    schedule = api_client.post(
        f"/backup/schedules/cluster/{cluster_id}",
        json={"job_type": "backup_full", "inventory_group_id": group_id, "repository": "s3_repo"},
    ).json()
    job = _wait_for_terminal(api_client, schedule["last_run_job_id"])
    return job, schedule


def test_get_job_reports_schedule_id_for_a_schedule_submitted_job(api_client, monkeypatch):
    cluster_id = _create_cluster(api_client)
    group_id = _create_group(api_client, cluster_id)
    job, schedule = _submit_via_one_shot_schedule(api_client, monkeypatch, cluster_id, group_id)

    assert job["schedule_id"] == schedule["id"]


def test_backup_history_filters_by_schedule_id(api_client, monkeypatch):
    cluster_id = _create_cluster(api_client)
    group_id = _create_group(api_client, cluster_id)
    scheduled_job, schedule = _submit_via_one_shot_schedule(api_client, monkeypatch, cluster_id, group_id)
    _submit_backup_full(api_client, monkeypatch, cluster_id, group_id=1)

    response = api_client.get(
        f"/backup/history/cluster/{cluster_id}", params={"schedule_id": schedule["id"]}
    )

    assert response.status_code == 200
    body = response.json()
    assert [job["id"] for job in body] == [scheduled_job["id"]]


def test_backup_history_filters_by_schedule_id_excludes_directly_submitted_jobs(api_client, monkeypatch):
    """A job submitted before schedule linkage existed (or submitted directly, never
    through a schedule) has schedule_id = null and must never match a schedule_id filter,
    even though it belongs to the same cluster as a schedule-submitted job."""
    cluster_id = _create_cluster(api_client)
    group_id = _create_group(api_client, cluster_id)
    scheduled_job, schedule = _submit_via_one_shot_schedule(api_client, monkeypatch, cluster_id, group_id)
    direct_job = _submit_backup_full(api_client, monkeypatch, cluster_id, group_id=1)

    response = api_client.get(
        f"/backup/history/cluster/{cluster_id}", params={"schedule_id": schedule["id"]}
    )

    assert response.status_code == 200
    result_ids = [job["id"] for job in response.json()]
    assert result_ids == [scheduled_job["id"]]
    assert direct_job["id"] not in result_ids


def test_backup_history_filters_by_group_id_paginates(api_client, monkeypatch):
    cluster_id = _create_cluster(api_client)
    jobs = [_submit_backup_full(api_client, monkeypatch, cluster_id, group_id=1) for _ in range(3)]
    _submit_backup_full(api_client, monkeypatch, cluster_id, group_id=2)
    expected_order = list(reversed(jobs))

    response = api_client.get(
        f"/backup/history/cluster/{cluster_id}",
        params={"group_id": 1, "limit": 2, "offset": 1},
    )

    assert response.status_code == 200
    body = response.json()
    assert [job["id"] for job in body] == [expected_order[1]["id"], expected_order[2]["id"]]


def test_get_job_history_for_unknown_job_is_404(api_client):
    assert api_client.get("/job/999/history").status_code == 404


def test_get_job_history_empty_for_pending_job(api_client, monkeypatch):
    """A job with no recorded history yet (still PENDING) returns an empty list with 200."""
    release = threading.Event()

    def handler(cluster, params, job_id, on_progress=None):
        release.wait(timeout=2)
        return {}

    from starrocks_br.jobs import handlers

    _mock_group_check(monkeypatch)
    _mock_repository_check(monkeypatch)
    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", handler)

    cluster_id = _create_cluster(api_client)
    submitted = api_client.post(
        f"/backup/manual/full/cluster/{cluster_id}", json={"group_id": 1, "repository": "s3_repo"}
    ).json()

    try:
        response = api_client.get(f"/job/{submitted['id']}/history")
        assert response.status_code == 200
        assert response.json() == []
    finally:
        release.set()
        _wait_for_terminal(api_client, submitted["id"])


def test_get_backup_job_history_returns_time_ordered_entries(api_client, monkeypatch):
    from starrocks_br.dal.metadata import history
    from starrocks_br.jobs import handlers
    from starrocks_br.store.session import get_session_factory

    def handler(cluster, params, job_id, on_progress=None):
        history.append_backup_event(get_session_factory(), job_id, "SNAPSHOTING")
        history.append_backup_event(get_session_factory(), job_id, "UPLOADING")
        return {}

    _mock_group_check(monkeypatch)
    _mock_repository_check(monkeypatch)
    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", handler)

    cluster_id = _create_cluster(api_client)
    submitted = api_client.post(
        f"/backup/manual/full/cluster/{cluster_id}", json={"group_id": 1, "repository": "s3_repo"}
    ).json()

    _wait_for_terminal(api_client, submitted["id"])

    response = api_client.get(f"/job/{submitted['id']}/history")

    assert response.status_code == 200
    body = response.json()
    assert [entry["status"] for entry in body] == ["SNAPSHOTING", "UPLOADING"]
    assert all(entry["job_id"] == submitted["id"] for entry in body)


def test_get_restore_job_history_uses_restore_history_table(api_client, monkeypatch):
    from starrocks_br.dal.metadata import history
    from starrocks_br.jobs import handlers
    from starrocks_br.store.session import get_session_factory

    def handler(cluster, params, job_id, on_progress=None):
        history.append_restore_event(get_session_factory(), job_id, "DOWNLOADING")
        return {}

    monkeypatch.setitem(handlers.JOB_HANDLERS, "restore", handler)

    cluster_id = _create_cluster(api_client)
    submitted = api_client.post(
        f"/backup/manual/restore/cluster/{cluster_id}", json={"target_label": "some_label"}
    ).json()

    _wait_for_terminal(api_client, submitted["id"])

    response = api_client.get(f"/job/{submitted['id']}/history")

    assert response.status_code == 200
    body = response.json()
    assert [entry["status"] for entry in body] == ["DOWNLOADING"]


def test_get_prune_job_history_is_empty_no_log_table(api_client, monkeypatch):
    """Retention/prune jobs have no log table; history is an empty 200, not 404."""
    from starrocks_br.jobs import handlers

    _mock_group_check(monkeypatch)
    monkeypatch.setitem(
        handlers.JOB_HANDLERS, "prune", lambda cluster, params, job_id, on_progress=None: {}
    )

    cluster_id = _create_cluster(api_client)
    submitted = api_client.post(
        f"/backup/manual/prune/cluster/{cluster_id}", json={"group_id": 1, "keep_last": 1}
    ).json()

    _wait_for_terminal(api_client, submitted["id"])

    response = api_client.get(f"/job/{submitted['id']}/history")

    assert response.status_code == 200
    assert response.json() == []
