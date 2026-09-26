import datetime

import pytest

from starrocks_br import exceptions
from starrocks_br.commands.schedules import compute_next_run_at, run_due_schedules
from starrocks_br.store.models import Base, Cluster, InventoryGroup, Job, Schedule
from starrocks_br.store.session import get_engine, session_scope


@pytest.fixture
def sqlite_store(tmp_path, monkeypatch):
    monkeypatch.setenv("STARROCKS_BR_DATABASE_URL", f"sqlite:///{tmp_path / 'schedules.db'}")
    from starrocks_br.store import session as session_module

    session_module.reset_engine_cache()
    Base.metadata.create_all(get_engine())
    yield
    session_module.reset_engine_cache()


def _make_cluster(session) -> Cluster:
    cluster = Cluster(
        name="c1", host="h", port=9030, user="u", password_encrypted="enc",
    )
    session.add(cluster)
    session.flush()
    return cluster


def _make_group(session, cluster: Cluster, name: str = "g1") -> InventoryGroup:
    group = InventoryGroup(cluster_id=cluster.id, name=name)
    session.add(group)
    session.flush()
    return group


def _make_job(session, cluster: Cluster) -> Job:
    job = Job(cluster_id=cluster.id, job_type="backup_full", backend="thread", params_json="{}")
    session.add(job)
    session.flush()
    return job


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def test_compute_next_run_at_raises_invalid_cadence_error():
    with pytest.raises(exceptions.InvalidCadenceError, match="not-a-cadence"):
        compute_next_run_at("not-a-cadence")


def test_run_due_schedules_triggers_due_schedule_and_advances_next_run_at(sqlite_store, mocker):
    with session_scope() as session:
        cluster = _make_cluster(session)
        group = _make_group(session, cluster)
        job = _make_job(session, cluster)
        submit_job = mocker.patch("starrocks_br.commands.schedules.submit_job", return_value=job)

        schedule = Schedule(
            cluster_id=cluster.id,
            job_type="backup_full",
            inventory_group_id=group.id,
            repository="repo",
            cadence="* * * * *",
            backend="thread",
            enabled=True,
            next_run_at=_utcnow() - datetime.timedelta(minutes=1),
        )
        session.add(schedule)
        session.flush()

        triggered_job_ids, triggered_count = run_due_schedules(session, _utcnow())

        assert triggered_job_ids == [job.id]
        assert triggered_count == 1
        assert schedule.last_run_job_id == job.id
        assert schedule.next_run_at > _utcnow() - datetime.timedelta(seconds=5)
        submit_job.assert_called_once()
        args = submit_job.call_args.args
        assert args[1].id == cluster.id
        assert args[2] == "backup_full"
        assert args[3] == {"group_id": group.id, "repository": "repo"}
        assert args[4] == "thread"


def test_run_due_schedules_skips_not_yet_due_schedule(sqlite_store, mocker):
    submit_job = mocker.patch("starrocks_br.commands.schedules.submit_job")

    with session_scope() as session:
        cluster = _make_cluster(session)
        group = _make_group(session, cluster)
        session.add(
            Schedule(
                cluster_id=cluster.id,
                job_type="backup_full",
                inventory_group_id=group.id,
                repository="repo",
                cadence="* * * * *",
                backend="thread",
                enabled=True,
                next_run_at=_utcnow() + datetime.timedelta(hours=1),
            )
        )
        session.flush()

        triggered_job_ids, triggered_count = run_due_schedules(session, _utcnow())

        assert triggered_job_ids == []
        assert triggered_count == 0
        submit_job.assert_not_called()


def test_run_due_schedules_is_idempotent_when_already_advanced(sqlite_store, mocker):
    """Simulates a concurrent run-due call already having advanced the row."""
    with session_scope() as session:
        cluster = _make_cluster(session)
        group = _make_group(session, cluster)
        job = _make_job(session, cluster)
        submit_job = mocker.patch("starrocks_br.commands.schedules.submit_job", return_value=job)

        now = _utcnow()
        schedule = Schedule(
            cluster_id=cluster.id,
            job_type="backup_full",
            inventory_group_id=group.id,
            repository="repo",
            cadence="* * * * *",
            backend="thread",
            enabled=True,
            next_run_at=now - datetime.timedelta(minutes=1),
        )
        session.add(schedule)
        session.flush()

        # First call advances the row for real.
        run_due_schedules(session, now)
        session.flush()

        # A second call at the same instant must not re-trigger it.
        submit_job.reset_mock()
        triggered_job_ids, triggered_count = run_due_schedules(session, now)

        assert triggered_job_ids == []
        assert triggered_count == 0
        submit_job.assert_not_called()
