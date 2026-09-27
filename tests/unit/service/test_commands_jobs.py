from starrocks_br.commands.jobs import list_jobs
from starrocks_br.store.models import Job


def _add_job(session, cluster_id, job_type="backup_full", status="SUCCESS"):
    job = Job(cluster_id=cluster_id, job_type=job_type, backend="thread", status=status)
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
