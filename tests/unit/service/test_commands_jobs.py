from starrocks_br.commands.jobs import list_jobs, submit_job
from starrocks_br.store.models import Job


def _add_job(session, cluster_id, job_type="backup_full", status="SUCCESS", group_id=None):
    job = Job(
        cluster_id=cluster_id, job_type=job_type, backend="thread", status=status, group_id=group_id
    )
    session.add(job)
    session.commit()
    return job


def test_list_jobs_orders_most_recent_first(sqlite_session, make_cluster):
    cluster = make_cluster()
    first = _add_job(sqlite_session, cluster.id)
    second = _add_job(sqlite_session, cluster.id)
    third = _add_job(sqlite_session, cluster.id)

    result = list_jobs(sqlite_session, cluster.id)

    assert [job.id for job in result] == [third.id, second.id, first.id]


def test_list_jobs_filters_by_job_type(sqlite_session, make_cluster):
    cluster = make_cluster()
    _add_job(sqlite_session, cluster.id, job_type="backup_full")
    prune_job = _add_job(sqlite_session, cluster.id, job_type="prune")

    result = list_jobs(sqlite_session, cluster.id, job_type="prune")

    assert [job.id for job in result] == [prune_job.id]


def test_list_jobs_filters_by_job_type_list(sqlite_session, make_cluster):
    cluster = make_cluster()
    full_job = _add_job(sqlite_session, cluster.id, job_type="backup_full")
    incremental_job = _add_job(sqlite_session, cluster.id, job_type="backup_incremental")
    _add_job(sqlite_session, cluster.id, job_type="prune")

    result = list_jobs(sqlite_session, cluster.id, job_type=["backup_full", "backup_incremental"])

    assert {job.id for job in result} == {full_job.id, incremental_job.id}


def test_list_jobs_filters_by_status(sqlite_session, make_cluster):
    cluster = make_cluster()
    _add_job(sqlite_session, cluster.id, status="SUCCESS")
    failed_job = _add_job(sqlite_session, cluster.id, status="FAILED")

    result = list_jobs(sqlite_session, cluster.id, status="FAILED")

    assert [job.id for job in result] == [failed_job.id]


def test_list_jobs_scopes_to_cluster(sqlite_session, make_cluster):
    cluster_a = make_cluster(name="cluster-a")
    cluster_b = make_cluster(name="cluster-b")
    job_a = _add_job(sqlite_session, cluster_a.id)
    _add_job(sqlite_session, cluster_b.id)

    result = list_jobs(sqlite_session, cluster_a.id)

    assert [job.id for job in result] == [job_a.id]


def test_list_jobs_paginates_with_limit_and_offset(sqlite_session, make_cluster):
    cluster = make_cluster()
    jobs = [_add_job(sqlite_session, cluster.id) for _ in range(5)]
    expected_order = list(reversed(jobs))

    page = list_jobs(sqlite_session, cluster.id, limit=2, offset=2)

    assert [job.id for job in page] == [expected_order[2].id, expected_order[3].id]


def test_list_jobs_returns_empty_list_when_no_jobs(sqlite_session, make_cluster):
    cluster = make_cluster()

    assert list_jobs(sqlite_session, cluster.id) == []


def test_list_jobs_filters_by_job_id(sqlite_session, make_cluster):
    cluster = make_cluster()
    target = _add_job(sqlite_session, cluster.id)
    _add_job(sqlite_session, cluster.id)

    result = list_jobs(sqlite_session, cluster.id, job_id=target.id)

    assert [job.id for job in result] == [target.id]


def test_list_jobs_filters_by_job_id_with_no_match(sqlite_session, make_cluster):
    cluster = make_cluster()
    _add_job(sqlite_session, cluster.id)

    result = list_jobs(sqlite_session, cluster.id, job_id=999999)

    assert result == []


def test_list_jobs_filters_by_group_id(sqlite_session, make_cluster):
    cluster = make_cluster()
    group_1_job = _add_job(sqlite_session, cluster.id, group_id=1)
    _add_job(sqlite_session, cluster.id, group_id=2)

    result = list_jobs(sqlite_session, cluster.id, group_id=1)

    assert [job.id for job in result] == [group_1_job.id]


def test_list_jobs_filters_by_group_id_with_no_match(sqlite_session, make_cluster):
    cluster = make_cluster()
    _add_job(sqlite_session, cluster.id, group_id=1)

    result = list_jobs(sqlite_session, cluster.id, group_id=999999)

    assert result == []


def test_list_jobs_filters_by_group_id_excludes_jobs_with_no_recorded_group(
    sqlite_session, make_cluster
):
    """Jobs submitted before group_id tracking existed (or of a job type that never
    carried one) have group_id = NULL and must never match a group_id filter."""
    cluster = make_cluster()
    _add_job(sqlite_session, cluster.id, group_id=None)

    result = list_jobs(sqlite_session, cluster.id, group_id=1)

    assert result == []


class _StubBackend:
    name = "thread"

    def __init__(self):
        self.enqueued: list[int] = []

    def enqueue(self, job_id: int) -> None:
        self.enqueued.append(job_id)


def test_submit_job_persists_group_id_from_params(sqlite_session, make_cluster):
    from starrocks_br.jobs.backend import BackendRegistry, reset_registry, set_registry

    cluster = make_cluster()
    set_registry(BackendRegistry({"thread": _StubBackend()}, "thread"))
    try:
        job = submit_job(
            sqlite_session, cluster, "backup_full", {"group_id": 7, "repository": "s3_repo"}, None
        )
    finally:
        reset_registry()

    assert job.group_id == 7


def test_submit_job_leaves_group_id_none_when_params_have_no_group_id(sqlite_session, make_cluster):
    from starrocks_br.jobs.backend import BackendRegistry, reset_registry, set_registry

    cluster = make_cluster()
    set_registry(BackendRegistry({"thread": _StubBackend()}, "thread"))
    try:
        job = submit_job(sqlite_session, cluster, "restore", {"target_label": "x", "table": "t1"}, None)
    finally:
        reset_registry()

    assert job.group_id is None
