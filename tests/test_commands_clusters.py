import datetime

import pytest

from starrocks_br import exceptions
from starrocks_br.commands.clusters import delete_cluster
from starrocks_br.store.models import Base, Cluster, InventoryGroup, Job, Schedule
from starrocks_br.store.session import get_engine, session_scope


@pytest.fixture
def sqlite_store(tmp_path, monkeypatch):
    monkeypatch.setenv("STARROCKS_BR_DATABASE_URL", f"sqlite:///{tmp_path / 'clusters.db'}")
    from starrocks_br.store import session as session_module

    session_module.reset_engine_cache()
    Base.metadata.create_all(get_engine())
    yield
    session_module.reset_engine_cache()


def _make_cluster(session) -> Cluster:
    cluster = Cluster(
        name="c1",
        host="h",
        port=9030,
        user="u",
        password_encrypted="enc",
        database="db",
        repository="repo",
    )
    session.add(cluster)
    session.flush()
    return cluster


def test_delete_cluster_blocked_by_active_job(sqlite_store):
    with session_scope() as session:
        cluster = _make_cluster(session)
        session.add(
            Job(cluster_id=cluster.id, job_type="backup_full", backend="thread", params_json="{}")
        )
        session.flush()

        with pytest.raises(exceptions.ClusterHasActiveJobError):
            delete_cluster(session, cluster)


def test_delete_cluster_blocked_by_enabled_schedule(sqlite_store):
    with session_scope() as session:
        cluster = _make_cluster(session)
        group = InventoryGroup(cluster_id=cluster.id, name="g1")
        session.add(group)
        session.flush()
        session.add(
            Schedule(
                cluster_id=cluster.id,
                job_type="backup_full",
                inventory_group_id=group.id,
                cadence="0 0 * * *",
                backend="thread",
                enabled=True,
                next_run_at=datetime.datetime.now(datetime.timezone.utc),
            )
        )
        session.flush()

        with pytest.raises(exceptions.ClusterHasEnabledScheduleError):
            delete_cluster(session, cluster)


def test_delete_cluster_succeeds_when_idle(sqlite_store):
    with session_scope() as session:
        cluster = _make_cluster(session)

        delete_cluster(session, cluster)
        session.flush()

        assert session.get(Cluster, cluster.id) is None


