from starrocks_br.dal.metadata import concurrency
from starrocks_br.store.models import RunStatus


def test_active_jobs_for_scope_filters_by_scope_and_state(sqlite_session, make_cluster):
    cluster = make_cluster()
    sqlite_session.add(RunStatus(cluster_id=cluster.id, scope="backup", label="l1", state="ACTIVE"))
    sqlite_session.add(RunStatus(cluster_id=cluster.id, scope="backup", label="l2", state="CANCELLED"))
    sqlite_session.add(RunStatus(cluster_id=cluster.id, scope="restore", label="l3", state="ACTIVE"))
    sqlite_session.commit()

    result = concurrency.active_jobs_for_scope(sqlite_session, cluster.id, "backup")

    assert result == [("backup", "l1", "ACTIVE")]


def test_insert_active_job_persists_row(sqlite_session, make_cluster):
    cluster = make_cluster()

    concurrency.insert_active_job(sqlite_session, cluster.id, "backup", "l1")

    row = sqlite_session.query(RunStatus).filter_by(cluster_id=cluster.id, label="l1").one()
    assert row.state == "ACTIVE"


def test_cancel_stale_job_sets_cancelled(sqlite_session, make_cluster):
    cluster = make_cluster()
    concurrency.insert_active_job(sqlite_session, cluster.id, "backup", "l1")

    concurrency.cancel_stale_job(sqlite_session, cluster.id, "backup", "l1")

    row = sqlite_session.query(RunStatus).filter_by(cluster_id=cluster.id, label="l1").one()
    assert row.state == "CANCELLED"
    assert row.finished_at is not None


def test_cancel_stale_job_no_op_when_missing(sqlite_session, make_cluster):
    cluster = make_cluster()

    concurrency.cancel_stale_job(sqlite_session, cluster.id, "backup", "missing")


def test_complete_job_sets_final_state(sqlite_session, make_cluster):
    cluster = make_cluster()
    concurrency.insert_active_job(sqlite_session, cluster.id, "backup", "l1")

    concurrency.complete_job(sqlite_session, cluster.id, "backup", "l1", "FINISHED")

    row = sqlite_session.query(RunStatus).filter_by(cluster_id=cluster.id, label="l1").one()
    assert row.state == "FINISHED"
    assert row.finished_at is not None


def test_complete_job_no_op_when_missing(sqlite_session, make_cluster):
    cluster = make_cluster()

    concurrency.complete_job(sqlite_session, cluster.id, "backup", "missing", "FAILED")
