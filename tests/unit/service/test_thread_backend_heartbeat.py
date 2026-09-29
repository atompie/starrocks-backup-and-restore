import datetime
import threading
import time

import pytest

from starrocks_br.jobs import handlers, thread_backend
from starrocks_br.jobs.thread_backend import ThreadBackend
from starrocks_br.store import session as session_module
from starrocks_br.store.models import Base, Cluster, Job, JobStatus


@pytest.fixture
def cluster_id(tmp_path, monkeypatch):
    monkeypatch.setenv("STARROCKS_BR_DATABASE_URL", f"sqlite:///{tmp_path / 'hb.db'}")
    session_module.reset_engine_cache()
    Base.metadata.create_all(session_module.get_engine())
    with session_module.session_scope() as session:
        cluster = Cluster(name="c1", host="h", port=9030, user="u", password_encrypted="enc")
        session.add(cluster)
        session.flush()
        cluster_pk = cluster.id
    yield cluster_pk
    session_module.reset_engine_cache()


@pytest.fixture
def fast_heartbeat(monkeypatch):
    monkeypatch.setattr(thread_backend, "get_job_heartbeat_seconds", lambda: 0.05)


def _submit(cluster_id: int) -> int:
    """Insert a job as the dispatcher would have admitted it (`RUNNING`)."""
    with session_module.session_scope() as session:
        job = Job(
            cluster_id=cluster_id,
            job_type="backup_full",
            backend="thread",
            params_json="{}",
            status=JobStatus.RUNNING.value,
            started_at=datetime.datetime.now(datetime.timezone.utc),
            heartbeat_at=datetime.datetime.now(datetime.timezone.utc),
        )
        session.add(job)
        session.flush()
        return job.id


def _heartbeat(job_id: int):
    with session_module.session_scope() as session:
        return session.get(Job, job_id).heartbeat_at


def _wait_terminal(job_id: int, timeout: float = 3.0) -> Job:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with session_module.session_scope() as session:
            job = session.get(Job, job_id)
            if job.status in (JobStatus.SUCCESS.value, JobStatus.FAILED.value):
                session.expunge(job)
                return job
        time.sleep(0.02)
    raise TimeoutError(job_id)


def _heartbeat_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith("starrocks-br-heartbeat-")]


def test_heartbeat_advances_without_progress_callbacks(cluster_id, fast_heartbeat, monkeypatch):
    observed = []
    job_id_holder = {}

    def slow_handler(cluster, params, job_id, on_progress=None):
        job_id_holder["id"] = job_id
        observed.append(_heartbeat(job_id))
        time.sleep(0.4)
        observed.append(_heartbeat(job_id))
        return {}

    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", slow_handler)
    job_id = _submit(cluster_id)

    backend = ThreadBackend(max_workers=1)
    try:
        backend.enqueue(job_id)
        _wait_terminal(job_id)
    finally:
        backend.shutdown(wait=True)

    started, after_wait = observed
    assert started is not None
    assert after_wait > started


@pytest.mark.parametrize("fails", [False, True])
def test_heartbeat_thread_stops_when_handler_finishes(cluster_id, fast_heartbeat, monkeypatch, fails):
    def handler(cluster, params, job_id, on_progress=None):
        if fails:
            raise RuntimeError("boom")
        return {}

    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", handler)
    job_id = _submit(cluster_id)

    backend = ThreadBackend(max_workers=1)
    try:
        backend.enqueue(job_id)
        final = _wait_terminal(job_id)
    finally:
        backend.shutdown(wait=True)

    assert final.status == (JobStatus.FAILED.value if fails else JobStatus.SUCCESS.value)
    assert _heartbeat_threads() == []
    frozen = _heartbeat(job_id)
    time.sleep(0.2)
    assert _heartbeat(job_id) == frozen


def test_failed_heartbeat_write_does_not_fail_the_job(cluster_id, fast_heartbeat, monkeypatch):
    def handler(cluster, params, job_id, on_progress=None):
        time.sleep(0.2)
        return {"ok": True}

    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", handler)
    monkeypatch.setattr(
        thread_backend.jobs_dal,
        "touch_heartbeat",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db busy")),
    )
    job_id = _submit(cluster_id)

    backend = ThreadBackend(max_workers=1)
    try:
        backend.enqueue(job_id)
        final = _wait_terminal(job_id)
    finally:
        backend.shutdown(wait=True)

    assert final.status == JobStatus.SUCCESS.value
