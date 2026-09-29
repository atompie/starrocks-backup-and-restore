from unittest.mock import MagicMock

import pytest

from starrocks_br.commands import jobs as jobs_commands
from starrocks_br.store import session as session_module
from starrocks_br.store.models import Base, Cluster, Job, JobStatus


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("STARROCKS_BR_DATABASE_URL", f"sqlite:///{tmp_path / 'dispatch.db'}")
    session_module.reset_engine_cache()
    Base.metadata.create_all(session_module.get_engine())
    yield
    session_module.reset_engine_cache()


@pytest.fixture
def make_cluster(store):
    def _make(name: str) -> int:
        with session_module.session_scope() as session:
            cluster = Cluster(name=name, host="h", port=9030, user="u", password_encrypted="enc")
            session.add(cluster)
            session.flush()
            return cluster.id

    return _make


@pytest.fixture
def make_job(store):
    def _make(cluster_id: int, job_type="backup_full", status=JobStatus.PENDING.value) -> int:
        with session_module.session_scope() as session:
            job = Job(
                cluster_id=cluster_id, job_type=job_type, backend="thread", params_json="{}", status=status
            )
            session.add(job)
            session.flush()
            return job.id

    return _make


@pytest.fixture
def backend(mocker):
    thread_backend = MagicMock()
    registry = MagicMock()
    registry.get.return_value = thread_backend
    mocker.patch.object(jobs_commands, "get_registry", return_value=registry)
    return thread_backend


def _status(job_id: int) -> str:
    with session_module.session_scope() as session:
        return session.get(Job, job_id).status


def _finish(job_id: int) -> None:
    with session_module.session_scope() as session:
        session.get(Job, job_id).status = JobStatus.SUCCESS.value


def test_two_jobs_on_one_cluster_run_one_after_another(make_cluster, make_job, backend):
    cluster = make_cluster("c1")
    first = make_job(cluster)
    second = make_job(cluster)

    tick_one = jobs_commands.dispatch_pending_jobs()
    tick_two = jobs_commands.dispatch_pending_jobs()

    assert tick_one.admitted == [first]
    assert tick_two.admitted == []
    assert _status(first) == JobStatus.RUNNING.value
    assert _status(second) == JobStatus.PENDING.value

    _finish(first)
    tick_three = jobs_commands.dispatch_pending_jobs()

    assert tick_three.admitted == [second]
    assert [c.args[0] for c in backend.enqueue.call_args_list] == [first, second]


def test_jobs_on_different_clusters_are_admitted_in_one_pass(make_cluster, make_job, backend):
    first = make_job(make_cluster("a"))
    second = make_job(make_cluster("b"))

    summary = jobs_commands.dispatch_pending_jobs()

    assert sorted(summary.admitted) == sorted([first, second])
    assert _status(first) == _status(second) == JobStatus.RUNNING.value


def test_restore_is_admitted_before_backup_and_backup_before_other_work(make_cluster, make_job, backend):
    cluster = make_cluster("c1")
    cleanup = make_job(cluster, "schedule_cleanup")
    backup = make_job(cluster, "backup_full")
    restore = make_job(cluster, "restore")
    order = []

    for _ in range(3):
        admitted = jobs_commands.dispatch_pending_jobs().admitted
        order.extend(admitted)
        _finish(admitted[0])

    assert order == [restore, backup, cleanup]


def test_a_busy_cluster_admits_nothing_and_fails_nothing(make_cluster, make_job, backend):
    cluster = make_cluster("c1")
    make_job(cluster, status=JobStatus.RUNNING.value)
    waiting = make_job(cluster, "restore")

    summary = jobs_commands.dispatch_pending_jobs()

    assert summary.admitted == summary.failed == []
    assert _status(waiting) == JobStatus.PENDING.value
    backend.enqueue.assert_not_called()


def test_a_lost_claim_is_not_enqueued(make_cluster, make_job, backend, mocker):
    job = make_job(make_cluster("c1"))
    mocker.patch.object(jobs_commands.jobs_dal, "claim_pending_job", return_value=False)

    summary = jobs_commands.dispatch_pending_jobs()

    assert summary.admitted == []
    backend.enqueue.assert_not_called()
    assert _status(job) == JobStatus.PENDING.value


def test_an_enqueue_failure_fails_only_that_job_and_the_pass_continues(make_cluster, make_job, backend):
    bad = make_job(make_cluster("a"))
    good = make_job(make_cluster("b"))
    backend.enqueue.side_effect = [RuntimeError("backend down"), None]

    summary = jobs_commands.dispatch_pending_jobs()

    assert summary.failed == [bad]
    assert summary.admitted == [good]
    with session_module.session_scope() as session:
        failed = session.get(Job, bad)
        assert failed.status == JobStatus.FAILED.value
        assert "backend down" in failed.error_message


def test_submit_job_only_queues_the_job(make_cluster, backend, mocker):
    cluster_id = make_cluster("c1")
    registry = jobs_commands.get_registry()
    registry.resolve.return_value = "thread"

    with session_module.session_scope() as session:
        cluster = session.get(Cluster, cluster_id)
        job = jobs_commands.submit_job(session, cluster, "restore", {"target_label": "x"}, None)
        job_id = job.id

    assert _status(job_id) == JobStatus.PENDING.value
    backend.enqueue.assert_not_called()


def test_two_passes_holding_the_same_pending_job_enqueue_it_once(make_cluster, make_job, backend, mocker):
    """Overlapping dispatchers: both read the job as `PENDING`, only one conditional claim wins."""
    cluster = make_cluster("c1")
    job = make_job(cluster)
    with session_module.session_scope() as session:
        stale_read = session.get(Job, job)
        session.expunge(stale_read)
    mocker.patch.object(jobs_commands.jobs_dal, "cluster_has_running_job", return_value=False)
    mocker.patch.object(jobs_commands.jobs_dal, "next_pending_job", return_value=stale_read)

    first = jobs_commands.dispatch_pending_jobs()
    second = jobs_commands.dispatch_pending_jobs()

    assert first.admitted == [job]
    assert second.admitted == []
    backend.enqueue.assert_called_once_with(job)
    assert _status(job) == JobStatus.RUNNING.value
