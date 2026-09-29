import threading
import time
import uuid

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


def _mock_schedule_repository_check(monkeypatch):
    """Bypass the synchronous live repository-existence check for schedule creation.

    Manual full/incremental backup submission is retired - a backup job is now only ever
    created through a schedule (one-shot for an immediate full backup, recurring for
    incremental), so tests that need a running backup job go through schedule creation.
    """
    from starrocks_br.api.routes import schedules as schedules_module

    monkeypatch.setattr(schedules_module, "ensure_repository_exists", lambda cluster, repository_name: None)


def _create_one_shot_backup_full(api_client, cluster_id, group_id=None) -> dict:
    """Create a one-shot schedule and return the PENDING `backup_full` Job it submitted.

    Manual full backup submission is retired; a one-shot schedule is the only way left to
    submit an immediate backup_full job. `Schedule.inventory_group_id` is a real FK, so a
    group is created automatically when the caller doesn't need a specific one. Caller is
    responsible for having already mocked group/repository existence and any job handler it
    needs.
    """
    if group_id is None:
        group_id = _create_group(api_client, cluster_id)
    schedule = api_client.post(
        f"/backup/schedules/cluster/{cluster_id}",
        json={"job_type": "backup_full", "inventory_group_id": group_id, "repository": "s3_repo"},
    ).json()
    return api_client.get(f"/job/{schedule['last_run_job_id']}").json()


def _wait_for_terminal(api_client, job_id, timeout=2.0):
    """Poll until the job is terminal, running a scheduler dispatch pass on every iteration.

    Jobs only start when a tick admits them, so this stands in for repeated ticks.
    """
    from starrocks_br.commands.jobs import dispatch_pending_jobs

    deadline = time.time() + timeout
    while time.time() < deadline:
        dispatch_pending_jobs()
        body = api_client.get(f"/job/{job_id}").json()
        if body["status"] in ("SUCCESS", "FAILED"):
            return body
        time.sleep(0.02)
    raise TimeoutError("job did not finish in time")


def test_manual_backup_full_route_is_retired(api_client):
    response = api_client.post(
        "/backup/manual/full/cluster/1", json={"group_id": 1, "repository": "s3_repo"}
    )
    assert response.status_code == 404


def test_manual_backup_incremental_route_is_retired(api_client):
    response = api_client.post(
        "/backup/manual/incremental/cluster/1", json={"group_id": 1, "repository": "s3_repo"}
    )
    assert response.status_code == 404


def test_get_unknown_job_is_404(api_client):
    assert api_client.get("/job/999").status_code == 404


def test_submit_then_poll_to_terminal_state_success(api_client, monkeypatch):
    from starrocks_br.jobs import handlers

    def handler(cluster, params, job_id, on_progress=None):
        return {"label": "done"}

    _mock_group_check(monkeypatch)
    _mock_schedule_repository_check(monkeypatch)
    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", handler)

    cluster_id = _create_cluster(api_client)
    group_id = _create_group(api_client, cluster_id)
    schedule = api_client.post(
        f"/backup/schedules/cluster/{cluster_id}",
        json={"job_type": "backup_full", "inventory_group_id": group_id, "repository": "s3_repo"},
    ).json()

    final = _wait_for_terminal(api_client, schedule["last_run_job_id"])

    assert final["status"] == "SUCCESS"
    assert final["started_at"] is not None
    assert final["finished_at"] is not None
    assert final["schedule_id"] == schedule["id"]
    assert final["group_id"] == group_id
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
    _mock_schedule_repository_check(monkeypatch)
    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", handler)

    cluster_id = _create_cluster(api_client)
    group_id = _create_group(api_client, cluster_id)
    schedule = api_client.post(
        f"/backup/schedules/cluster/{cluster_id}",
        json={"job_type": "backup_full", "inventory_group_id": group_id, "repository": "s3_repo"},
    ).json()
    submitted = {"id": schedule["last_run_job_id"]}

    from starrocks_br.commands.jobs import dispatch_pending_jobs

    deadline = time.time() + 2
    seen_progress = None
    while time.time() < deadline:
        dispatch_pending_jobs()
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
    _mock_schedule_repository_check(monkeypatch)
    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", handler)

    cluster_id = _create_cluster(api_client)
    group_id = _create_group(api_client, cluster_id)
    schedule = api_client.post(
        f"/backup/schedules/cluster/{cluster_id}",
        json={"job_type": "backup_full", "inventory_group_id": group_id, "repository": "s3_repo"},
    ).json()
    submitted = {"id": schedule["last_run_job_id"]}

    from starrocks_br.commands.jobs import dispatch_pending_jobs

    deadline = time.time() + 2
    seen_running = None
    while time.time() < deadline:
        dispatch_pending_jobs()
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
    _mock_schedule_repository_check(monkeypatch)
    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", failing_handler)

    cluster_id = _create_cluster(api_client)
    group_id = _create_group(api_client, cluster_id)
    schedule = api_client.post(
        f"/backup/schedules/cluster/{cluster_id}",
        json={"job_type": "backup_full", "inventory_group_id": group_id, "repository": "s3_repo"},
    ).json()

    final = _wait_for_terminal(api_client, schedule["last_run_job_id"])

    assert final["status"] == "FAILED"
    assert final["error_message"] == "connection refused"


def test_backend_override_is_honored(api_client, monkeypatch):
    from starrocks_br.jobs import handlers

    _mock_group_check(monkeypatch)
    _mock_schedule_repository_check(monkeypatch)
    monkeypatch.setitem(
        handlers.JOB_HANDLERS, "backup_full", lambda cluster, params, job_id, on_progress=None: {}
    )

    cluster_id = _create_cluster(api_client)
    group_id = _create_group(api_client, cluster_id)
    response = api_client.post(
        f"/backup/schedules/cluster/{cluster_id}",
        json={
            "job_type": "backup_full",
            "inventory_group_id": group_id,
            "repository": "s3_repo",
            "backend": "thread",
        },
    )

    assert response.status_code == 201
    assert response.json()["backend"] == "thread"


def test_unrecognized_backend_value_is_rejected_with_422(api_client, monkeypatch):
    """A backend value outside {"thread", "job"} is rejected at the schema layer,
    before any group/repository/enabled-backend check runs."""
    cluster_id = _create_cluster(api_client)

    response = api_client.post(
        f"/backup/schedules/cluster/{cluster_id}",
        json={
            "job_type": "backup_full",
            "inventory_group_id": 1,
            "repository": "s3_repo",
            "backend": "kafka",
        },
    )

    assert response.status_code == 422


def test_recognized_but_disabled_backend_is_rejected_with_422(api_client, monkeypatch):
    """"job" is a recognized backend identifier but not enabled in this test server
    (STARROCKS_BR_ENABLED_BACKENDS=thread) - it must be rejected by the enabled-
    backend check, distinct from the schema-level enum check above."""
    _mock_group_check(monkeypatch)
    _mock_schedule_repository_check(monkeypatch)
    cluster_id = _create_cluster(api_client)
    group_id = _create_group(api_client, cluster_id)

    response = api_client.post(
        f"/backup/schedules/cluster/{cluster_id}",
        json={
            "job_type": "backup_full",
            "inventory_group_id": group_id,
            "repository": "s3_repo",
            "backend": "job",
        },
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


def test_submit_restore_from_a_backup_dropped_by_retention_is_409(api_client):
    import datetime

    from starrocks_br.store.models import BackupReference, Job
    from starrocks_br.store.session import session_scope

    cluster_id = _create_cluster(api_client)
    now = datetime.datetime.now(datetime.timezone.utc)
    with session_scope() as session:
        job = Job(
            cluster_id=cluster_id, job_type="backup_full", backend="thread", params_json="{}",
            status="SUCCESS", label="dropped_label", repository="repo", finished_at=now,
        )
        session.add(job)
        session.flush()
        session.add(
            BackupReference(
                job_id=job.id, repository="repo", snapshot_label="dropped_label", snapshot_timestamp=now,
                database_name="db", table_name="t", partition_name="p", deleted_at=now,
            )
        )

    response = api_client.post(
        f"/backup/manual/restore/cluster/{cluster_id}", json={"target_label": "dropped_label"}
    )

    assert response.status_code == 409
    with session_scope() as session:
        assert session.query(Job).filter_by(job_type="restore").count() == 0


def test_manual_prune_route_is_retired(api_client):
    cluster_id = _create_cluster(api_client)

    response = api_client.post(
        f"/backup/manual/prune/cluster/{cluster_id}", json={"group_id": 1, "keep_last": 3}
    )

    assert response.status_code == 404
    listing = api_client.get(f"/backup/history/cluster/{cluster_id}", params={"job_type": "prune"})
    assert listing.json() == []


def _submit_backup_full(api_client, monkeypatch, cluster_id, group_id=None):
    """Submit a one-shot `backup_full` schedule and return the terminal Job it created.

    `Schedule.inventory_group_id` is a real FK (unlike the retired manual route's plain
    `Job.group_id` int), so a group must actually exist - one is created automatically when
    the caller doesn't need a specific group id.
    """
    if group_id is None:
        group_id = _create_group(api_client, cluster_id)
    job, _schedule = _submit_via_one_shot_schedule(api_client, monkeypatch, cluster_id, group_id)
    return job


def _add_retention_job(cluster_id, group_id=None, events=()):
    """Insert a finished retention job (the tick, not an API call, creates these) and return it as a dict."""
    from starrocks_br.store.models import Job, RetentionHistory
    from starrocks_br.store.session import session_scope

    with session_scope() as session:
        job = Job(
            cluster_id=cluster_id, job_type="retention", backend="thread", params_json="{}",
            status="SUCCESS", group_id=group_id,
        )
        session.add(job)
        session.flush()
        for status in events:
            session.add(RetentionHistory(job_id=job.id, status=status))
        return {"id": job.id}


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
    _add_retention_job(cluster_id)

    response = api_client.get(f"/backup/history/cluster/{cluster_id}")

    assert response.status_code == 200
    body = response.json()
    assert [job["id"] for job in body] == [backup_job["id"]]


def test_backup_history_filters_by_job_type(api_client, monkeypatch):
    cluster_id = _create_cluster(api_client)
    _submit_backup_full(api_client, monkeypatch, cluster_id)
    retention_job = _add_retention_job(cluster_id)

    response = api_client.get(f"/backup/history/cluster/{cluster_id}", params={"job_type": "retention"})

    assert response.status_code == 200
    body = response.json()
    assert [job["id"] for job in body] == [retention_job["id"]]


def test_backup_history_filters_by_status(api_client, monkeypatch):
    from starrocks_br.jobs import handlers

    cluster_id = _create_cluster(api_client)
    group_id = _create_group(api_client, cluster_id)
    _mock_group_check(monkeypatch)
    _mock_schedule_repository_check(monkeypatch)

    def _submit_one_shot():
        return api_client.post(
            f"/backup/schedules/cluster/{cluster_id}",
            json={"job_type": "backup_full", "inventory_group_id": group_id, "repository": "s3_repo"},
        ).json()

    monkeypatch.setitem(
        handlers.JOB_HANDLERS,
        "backup_full",
        lambda cluster, params, job_id, on_progress=None: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    failed_schedule = _submit_one_shot()
    failed = _wait_for_terminal(api_client, failed_schedule["last_run_job_id"])

    monkeypatch.setitem(
        handlers.JOB_HANDLERS, "backup_full", lambda cluster, params, job_id, on_progress=None: {}
    )
    succeeded_schedule = _submit_one_shot()
    _wait_for_terminal(api_client, succeeded_schedule["last_run_job_id"])

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
    group_1 = _create_group(api_client, cluster_id)
    group_2 = _create_group(api_client, cluster_id)
    group_1_job = _submit_backup_full(api_client, monkeypatch, cluster_id, group_id=group_1)
    _submit_backup_full(api_client, monkeypatch, cluster_id, group_id=group_2)

    response = api_client.get(f"/backup/history/cluster/{cluster_id}", params={"group_id": group_1})

    assert response.status_code == 200
    body = response.json()
    assert [job["id"] for job in body] == [group_1_job["id"]]


def test_backup_history_filters_by_group_id_with_no_match(api_client, monkeypatch):
    cluster_id = _create_cluster(api_client)
    _submit_backup_full(api_client, monkeypatch, cluster_id)

    response = api_client.get(f"/backup/history/cluster/{cluster_id}", params={"group_id": 999999})

    assert response.status_code == 200
    assert response.json() == []


def test_backup_history_filters_by_group_id_and_job_type_combined(api_client, monkeypatch):
    cluster_id = _create_cluster(api_client)
    group_id = _create_group(api_client, cluster_id)
    backup_job = _submit_backup_full(api_client, monkeypatch, cluster_id, group_id=group_id)
    _add_retention_job(cluster_id, group_id=group_id)

    response = api_client.get(
        f"/backup/history/cluster/{cluster_id}",
        params={"group_id": group_id, "job_type": "backup_full"},
    )

    assert response.status_code == 200
    body = response.json()
    assert [job["id"] for job in body] == [backup_job["id"]]


def _create_group(api_client, cluster_id, name=None) -> int:
    response = api_client.post(
        f"/inventories/cluster/{cluster_id}",
        json={
            "name": name or f"g-{uuid.uuid4().hex[:8]}",
            "tables": [{"database": "sales_db", "table": "*"}],
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def _submit_via_one_shot_schedule(api_client, monkeypatch, cluster_id, group_id) -> dict:
    from starrocks_br.jobs import handlers

    _mock_group_check(monkeypatch)
    _mock_schedule_repository_check(monkeypatch)
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
    _submit_backup_full(api_client, monkeypatch, cluster_id)

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
    direct_job = _submit_backup_full(api_client, monkeypatch, cluster_id)

    response = api_client.get(
        f"/backup/history/cluster/{cluster_id}", params={"schedule_id": schedule["id"]}
    )

    assert response.status_code == 200
    result_ids = [job["id"] for job in response.json()]
    assert result_ids == [scheduled_job["id"]]
    assert direct_job["id"] not in result_ids


def test_backup_history_filters_by_group_id_paginates(api_client, monkeypatch):
    cluster_id = _create_cluster(api_client)
    group_id = _create_group(api_client, cluster_id)
    jobs = [_submit_backup_full(api_client, monkeypatch, cluster_id, group_id=group_id) for _ in range(3)]
    _submit_backup_full(api_client, monkeypatch, cluster_id)
    expected_order = list(reversed(jobs))

    response = api_client.get(
        f"/backup/history/cluster/{cluster_id}",
        params={"group_id": group_id, "limit": 2, "offset": 1},
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
    _mock_schedule_repository_check(monkeypatch)
    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", handler)

    cluster_id = _create_cluster(api_client)
    submitted = _create_one_shot_backup_full(api_client, cluster_id)

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
    _mock_schedule_repository_check(monkeypatch)
    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", handler)

    cluster_id = _create_cluster(api_client)
    submitted = _create_one_shot_backup_full(api_client, cluster_id)

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


def test_get_retention_job_history_uses_retention_history_table(api_client):
    cluster_id = _create_cluster(api_client)
    job = _add_retention_job(
        cluster_id, events=("RETENTION_STARTED", "SNAPSHOT_DROPPED", "RETENTION_FINISHED")
    )

    response = api_client.get(f"/job/{job['id']}/history")

    assert response.status_code == 200
    body = response.json()
    assert [entry["status"] for entry in body] == ["RETENTION_STARTED", "SNAPSHOT_DROPPED", "RETENTION_FINISHED"]
    assert all(entry["job_id"] == job["id"] for entry in body)


def test_get_legacy_prune_job_history_is_empty_no_log_table(api_client):
    from starrocks_br.store.models import Job
    from starrocks_br.store.session import session_scope

    cluster_id = _create_cluster(api_client)
    with session_scope() as session:
        job = Job(cluster_id=cluster_id, job_type="prune", backend="thread", params_json="{}", status="SUCCESS")
        session.add(job)
        session.flush()
        job_id = job.id

    response = api_client.get(f"/job/{job_id}/history")

    assert response.status_code == 200
    assert response.json() == []


def test_get_job_references_for_unknown_job_is_404(api_client):
    assert api_client.get("/job/999/references").status_code == 404


def test_get_job_references_empty_for_pending_job(api_client, monkeypatch):
    """A job that hasn't finished yet (still PENDING) has an empty reference list with 200."""
    release = threading.Event()

    def handler(cluster, params, job_id, on_progress=None):
        release.wait(timeout=2)
        return {}

    from starrocks_br.jobs import handlers

    _mock_group_check(monkeypatch)
    _mock_schedule_repository_check(monkeypatch)
    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", handler)

    cluster_id = _create_cluster(api_client)
    submitted = _create_one_shot_backup_full(api_client, cluster_id)

    try:
        response = api_client.get(f"/job/{submitted['id']}/references")
        assert response.status_code == 200
        assert response.json() == []
    finally:
        release.set()
        _wait_for_terminal(api_client, submitted["id"])


def test_get_job_references_returns_recorded_rows_for_successful_job(api_client, monkeypatch):
    """A job that recorded references (post-`FINISHED`, per SPEC.md §16) exposes them via the API."""
    import datetime

    from starrocks_br.dal.metadata import backup_catalog
    from starrocks_br.store.session import get_session_factory

    def handler(cluster, params, job_id, on_progress=None):
        with get_session_factory()() as session:
            backup_catalog.record_references(
                session,
                job_id,
                "s3_repo",
                "label1",
                datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
                [{"database": "sales_db", "table": "orders", "partition_name": "p1"}],
            )
            session.commit()
        return {}

    from starrocks_br.jobs import handlers

    _mock_group_check(monkeypatch)
    _mock_schedule_repository_check(monkeypatch)
    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", handler)

    cluster_id = _create_cluster(api_client)
    submitted = _create_one_shot_backup_full(api_client, cluster_id)

    _wait_for_terminal(api_client, submitted["id"])

    response = api_client.get(f"/job/{submitted['id']}/references")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["job_id"] == submitted["id"]
    assert body[0]["repository"] == "s3_repo"
    assert body[0]["snapshot_label"] == "label1"
    assert body[0]["database"] == "sales_db"
    assert body[0]["table"] == "orders"
    assert body[0]["partition"] == "p1"


def test_get_job_references_empty_for_failed_job(api_client, monkeypatch):
    """A job that finished as FAILED has no references (SPEC.md §16)."""

    def handler(cluster, params, job_id, on_progress=None):
        raise RuntimeError("boom")

    from starrocks_br.jobs import handlers

    _mock_group_check(monkeypatch)
    _mock_schedule_repository_check(monkeypatch)
    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", handler)

    cluster_id = _create_cluster(api_client)
    submitted = _create_one_shot_backup_full(api_client, cluster_id)

    finished = _wait_for_terminal(api_client, submitted["id"])
    assert finished["status"] == "FAILED"

    response = api_client.get(f"/job/{submitted['id']}/references")

    assert response.status_code == 200
    assert response.json() == []
