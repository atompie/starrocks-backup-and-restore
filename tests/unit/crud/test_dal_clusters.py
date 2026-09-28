import datetime

from starrocks_br.dal.metadata import clusters
from starrocks_br.store.models import Schedule


def test_create_persists_cluster(sqlite_session):
    cluster = clusters.create(
        sqlite_session,
        name="c1",
        host="h",
        port=9030,
        user="u",
        password_encrypted="enc",
        default_backend="thread",
    )

    assert cluster.id is not None
    assert clusters.get(sqlite_session, cluster.id).name == "c1"


def test_list_all_orders_by_id(sqlite_session, make_cluster):
    first = make_cluster("a")
    second = make_cluster("b")

    result = clusters.list_all(sqlite_session)

    assert [c.id for c in result] == [first.id, second.id]


def test_get_missing_returns_none(sqlite_session):
    assert clusters.get(sqlite_session, 999) is None


def test_update_applies_fields(sqlite_session, make_cluster):
    cluster = make_cluster()

    updated = clusters.update(sqlite_session, cluster, {"host": "new-host"})

    assert updated.host == "new-host"
    assert clusters.get(sqlite_session, cluster.id).host == "new-host"


def test_has_active_job_true_for_pending(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    make_job(cluster.id, status="PENDING")

    assert clusters.has_active_job(sqlite_session, cluster.id) is True


def test_has_active_job_false_when_only_terminal(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    make_job(cluster.id, status="SUCCESS")

    assert clusters.has_active_job(sqlite_session, cluster.id) is False


def test_has_enabled_schedule_true(sqlite_session, make_cluster, make_group):
    cluster = make_cluster()
    group_id = make_group(cluster.id)
    sqlite_session.add(
        Schedule(
            cluster_id=cluster.id,
            job_type="backup_full",
            inventory_group_id=group_id,
            repository="repo",
            cadence="0 0 * * *",
            backend="thread",
            enabled=True,
            next_run_at=datetime.datetime.now(datetime.timezone.utc),
        )
    )
    sqlite_session.commit()

    assert clusters.has_enabled_schedule(sqlite_session, cluster.id) is True


def test_has_enabled_schedule_false_when_disabled(sqlite_session, make_cluster, make_group):
    cluster = make_cluster()
    group_id = make_group(cluster.id)
    sqlite_session.add(
        Schedule(
            cluster_id=cluster.id,
            job_type="backup_full",
            inventory_group_id=group_id,
            repository="repo",
            cadence="0 0 * * *",
            backend="thread",
            enabled=False,
            next_run_at=datetime.datetime.now(datetime.timezone.utc),
        )
    )
    sqlite_session.commit()

    assert clusters.has_enabled_schedule(sqlite_session, cluster.id) is False


def test_delete_removes_cluster(sqlite_session, make_cluster):
    cluster = make_cluster()
    cluster_id = cluster.id

    clusters.delete(sqlite_session, cluster)
    sqlite_session.flush()

    assert clusters.get(sqlite_session, cluster_id) is None
