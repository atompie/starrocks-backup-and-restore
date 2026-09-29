import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from starrocks_br.dal.metadata import jobs as jobs_dal
from starrocks_br.store.models import Base, Job, JobStatus

NOW = datetime.datetime(2026, 9, 29, 12, 0, 0)
CUTOFF = NOW - datetime.timedelta(seconds=180)
OLD = NOW - datetime.timedelta(seconds=600)
FRESH = NOW - datetime.timedelta(seconds=10)


@pytest.fixture
def make_job(sqlite_session, make_cluster):
    cluster = make_cluster()

    def _make(status: str, *, created_at=OLD, started_at=None, heartbeat_at=None) -> Job:
        job = Job(
            cluster_id=cluster.id,
            job_type="backup_full",
            backend="thread",
            status=status,
            created_at=created_at,
            started_at=started_at,
            heartbeat_at=heartbeat_at,
        )
        sqlite_session.add(job)
        sqlite_session.commit()
        return job

    return _make


def test_mark_running_sets_heartbeat(sqlite_session, make_job):
    job = make_job(JobStatus.PENDING.value)

    jobs_dal.mark_running(sqlite_session, job.id)

    assert job.heartbeat_at is not None
    assert job.heartbeat_at == job.started_at


def test_touch_heartbeat_updates_a_running_job(sqlite_session, make_job):
    job = make_job(JobStatus.RUNNING.value, started_at=OLD, heartbeat_at=OLD)

    jobs_dal.touch_heartbeat(sqlite_session, job.id, NOW)
    sqlite_session.refresh(job)

    assert job.heartbeat_at == NOW


def test_touch_heartbeat_ignores_a_finished_job(sqlite_session, make_job):
    job = make_job(JobStatus.SUCCESS.value, started_at=OLD, heartbeat_at=OLD)

    jobs_dal.touch_heartbeat(sqlite_session, job.id, NOW)
    sqlite_session.refresh(job)

    assert job.heartbeat_at == OLD


def test_list_stale_jobs_uses_heartbeat_then_started_then_created(sqlite_session, make_job):
    stale_running = make_job(JobStatus.RUNNING.value, started_at=OLD, heartbeat_at=OLD)
    live_running = make_job(JobStatus.RUNNING.value, started_at=OLD, heartbeat_at=FRESH)
    no_heartbeat_old_start = make_job(JobStatus.RUNNING.value, started_at=OLD)
    no_heartbeat_fresh_start = make_job(JobStatus.RUNNING.value, started_at=FRESH)
    stale_pending = make_job(JobStatus.PENDING.value, created_at=OLD)
    fresh_pending = make_job(JobStatus.PENDING.value, created_at=FRESH)

    stale_ids = {j.id for j in jobs_dal.list_stale_jobs(sqlite_session, CUTOFF)}

    assert stale_ids == {stale_running.id, no_heartbeat_old_start.id, stale_pending.id}
    assert live_running.id not in stale_ids
    assert no_heartbeat_fresh_start.id not in stale_ids
    assert fresh_pending.id not in stale_ids


def test_list_stale_jobs_ignores_terminal_jobs(sqlite_session, make_job):
    make_job(JobStatus.SUCCESS.value, created_at=OLD, started_at=OLD, heartbeat_at=OLD)
    make_job(JobStatus.FAILED.value, created_at=OLD, started_at=OLD, heartbeat_at=OLD)

    assert jobs_dal.list_stale_jobs(sqlite_session, CUTOFF) == []


def test_claim_stale_job_succeeds_once(sqlite_session, make_job):
    job = make_job(JobStatus.RUNNING.value, started_at=OLD, heartbeat_at=OLD)

    first = jobs_dal.claim_stale_job(sqlite_session, job.id, JobStatus.RUNNING.value, CUTOFF, NOW)
    second = jobs_dal.claim_stale_job(sqlite_session, job.id, JobStatus.RUNNING.value, CUTOFF, NOW)

    assert first is True
    assert second is False


def test_claim_refuses_a_live_job(sqlite_session, make_job):
    job = make_job(JobStatus.RUNNING.value, started_at=OLD, heartbeat_at=FRESH)

    assert jobs_dal.claim_stale_job(sqlite_session, job.id, JobStatus.RUNNING.value, CUTOFF, NOW) is False


def test_claim_refuses_a_job_whose_status_changed(sqlite_session, make_job):
    job = make_job(JobStatus.SUCCESS.value, started_at=OLD, heartbeat_at=OLD)

    assert jobs_dal.claim_stale_job(sqlite_session, job.id, JobStatus.RUNNING.value, CUTOFF, NOW) is False


def test_concurrent_claim_from_two_sessions_has_one_winner(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'claim.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    with factory() as setup:
        from starrocks_br.store.models import Cluster

        cluster = Cluster(name="c", host="h", port=9030, user="u", password_encrypted="x")
        setup.add(cluster)
        setup.flush()
        job = Job(
            cluster_id=cluster.id,
            job_type="backup_full",
            backend="thread",
            status=JobStatus.RUNNING.value,
            created_at=OLD,
            started_at=OLD,
            heartbeat_at=OLD,
        )
        setup.add(job)
        setup.commit()
        job_id = job.id

    with factory() as a, factory() as b:
        won_a = jobs_dal.claim_stale_job(a, job_id, JobStatus.RUNNING.value, CUTOFF, NOW)
        a.commit()
        won_b = jobs_dal.claim_stale_job(b, job_id, JobStatus.RUNNING.value, CUTOFF, NOW)
        b.commit()

    assert [won_a, won_b] == [True, False]
