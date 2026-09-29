import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from starrocks_br import reconcile
from starrocks_br.commands import jobs as jobs_commands
from starrocks_br.store import session as session_module
from starrocks_br.store.models import (
    BackupHistory,
    BackupReference,
    Base,
    Cluster,
    Job,
    JobStatus,
    RestoreHistory,
    RetentionHistory,
    RunStatus,
)

NOW = datetime.datetime.now(datetime.timezone.utc)
STALE = NOW - datetime.timedelta(hours=1)
FRESH = NOW - datetime.timedelta(seconds=5)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("STARROCKS_BR_DATABASE_URL", f"sqlite:///{tmp_path / 'recon.db'}")
    session_module.reset_engine_cache()
    Base.metadata.create_all(session_module.get_engine())
    with session_module.session_scope() as session:
        cluster = Cluster(name="c1", host="h", port=9030, user="u", password_encrypted="enc")
        session.add(cluster)
        session.flush()
        cluster_id = cluster.id
    yield cluster_id
    session_module.reset_engine_cache()


@pytest.fixture
def make_job(store):
    def _make(status=JobStatus.RUNNING.value, *, job_type="backup_full", label="db_20260929_full",
              heartbeat_at=STALE, created_at=STALE, params_json='{"group_id": 1}', **extra) -> int:
        with session_module.session_scope() as session:
            job = Job(
                cluster_id=store, job_type=job_type, backend="thread", status=status, label=label,
                group_id=1, params_json=params_json, created_at=created_at,
                started_at=heartbeat_at if status == JobStatus.RUNNING.value else None,
                heartbeat_at=heartbeat_at if status == JobStatus.RUNNING.value else None, **extra,
            )
            session.add(job)
            session.flush()
            return job.id

    return _make


@pytest.fixture
def add_slot(store):
    def _add(label="db_20260929_full") -> None:
        with session_module.session_scope() as session:
            session.add(RunStatus(cluster_id=store, scope="backup", label=label, state="ACTIVE"))

    return _add


@pytest.fixture
def starrocks(mocker):
    """Patch the StarRocks-facing seams; tests set `.state` to what SHOW BACKUP/RESTORE reports."""
    handle = MagicMock()
    handle.state = None
    mocker.patch.object(jobs_commands, "connect", return_value=MagicMock())
    handle.lookup = mocker.patch.object(
        jobs_commands.reconcile, "find_operation_state", side_effect=lambda *a, **k: handle.state
    )
    mocker.patch.object(jobs_commands.planner, "resolve_group_databases", return_value=["db"])
    mocker.patch.object(jobs_commands.restore, "find_restore_pair", return_value=["full_x", "inc_y"])
    return handle


@pytest.fixture
def registry(mocker):
    backend = MagicMock()
    reg = MagicMock()
    reg.get.return_value = backend
    mocker.patch.object(jobs_commands, "get_registry", return_value=reg)
    return backend


def _job(job_id: int) -> Job:
    with session_module.session_scope() as session:
        job = session.get(Job, job_id)
        session.expunge(job)
        return job


def _slot_state(label="db_20260929_full") -> str:
    with session_module.session_scope() as session:
        return session.scalar(select(RunStatus.state).where(RunStatus.label == label))


def test_live_running_job_is_left_completely_alone(make_job, add_slot, starrocks):
    job_id = make_job(heartbeat_at=FRESH)
    add_slot()

    summary = jobs_commands.reconcile_stale_jobs()

    assert summary.failed == summary.left_running == []
    assert _job(job_id).status == JobStatus.RUNNING.value
    assert _slot_state() == "ACTIVE"
    starrocks.lookup.assert_not_called()


def test_pending_job_is_never_touched_by_reconciliation_whatever_its_age(make_job, registry):
    old_pending = make_job(JobStatus.PENDING.value, created_at=STALE)
    fresh_pending = make_job(JobStatus.PENDING.value, created_at=FRESH)

    summary = jobs_commands.reconcile_stale_jobs()

    assert summary.failed == summary.left_running == summary.skipped == []
    assert _job(old_pending).status == JobStatus.PENDING.value
    assert _job(fresh_pending).status == JobStatus.PENDING.value
    registry.enqueue.assert_not_called()


def test_finished_stale_backup_is_failed_not_promoted(make_job, add_slot, starrocks):
    job_id = make_job()
    add_slot()
    starrocks.state = "FINISHED"

    summary = jobs_commands.reconcile_stale_jobs()

    job = _job(job_id)
    assert summary.failed == [job_id]
    assert job.status == JobStatus.FAILED.value
    assert "db_20260929_full" in job.error_message
    assert "references" in job.error_message
    with session_module.session_scope() as session:
        assert session.scalars(select(BackupReference)).all() == []
        events = session.scalars(select(BackupHistory).where(BackupHistory.job_id == job_id)).all()
    assert [e.status for e in events] == ["FAILED"]
    assert _slot_state() == "FAILED"


def test_cancelled_stale_backup_fails_and_releases_slot(make_job, add_slot, starrocks):
    job_id = make_job()
    add_slot()
    starrocks.state = "CANCELLED"

    jobs_commands.reconcile_stale_jobs()

    assert _job(job_id).status == JobStatus.FAILED.value
    assert _slot_state() == "CANCELLED"


def test_backup_missing_from_starrocks_fails(make_job, add_slot, starrocks):
    job_id = make_job()
    add_slot()
    starrocks.state = None

    jobs_commands.reconcile_stale_jobs()

    assert _job(job_id).status == JobStatus.FAILED.value
    assert "no matching operation" in _job(job_id).error_message


def test_backup_still_active_in_starrocks_stays_running(make_job, add_slot, starrocks):
    job_id = make_job()
    add_slot()
    starrocks.state = "UPLOADING"

    summary = jobs_commands.reconcile_stale_jobs()

    assert summary.left_running == [job_id]
    assert _job(job_id).status == JobStatus.RUNNING.value
    assert _slot_state() == "ACTIVE"


def test_active_job_is_not_rechecked_until_stale_again(make_job, starrocks):
    make_job()
    starrocks.state = "UPLOADING"

    jobs_commands.reconcile_stale_jobs()
    jobs_commands.reconcile_stale_jobs()

    assert starrocks.lookup.call_count == 1


def test_stale_backup_without_label_fails_without_contacting_starrocks(make_job, starrocks):
    job_id = make_job(label=None)

    jobs_commands.reconcile_stale_jobs()

    assert _job(job_id).status == JobStatus.FAILED.value
    starrocks.lookup.assert_not_called()


def test_stale_non_backup_job_is_failed_with_reason(make_job, starrocks):
    job_id = make_job(job_type="schedule_cleanup", label=None)

    jobs_commands.reconcile_stale_jobs()

    job = _job(job_id)
    assert job.status == JobStatus.FAILED.value
    assert "resubmit" in job.error_message
    starrocks.lookup.assert_not_called()


def test_unreachable_cluster_leaves_job_running_and_continues(make_job, add_slot, starrocks, mocker):
    unreachable = make_job(label="a_full")
    add_slot("a_full")
    other = make_job(job_type="schedule_cleanup", label=None)
    mocker.patch.object(jobs_commands, "connect", side_effect=ConnectionError("no route"))

    summary = jobs_commands.reconcile_stale_jobs()

    assert unreachable in summary.left_running
    assert _job(unreachable).status == JobStatus.RUNNING.value
    assert _slot_state("a_full") == "ACTIVE"
    assert _job(other).status == JobStatus.FAILED.value


def test_stale_restore_is_failed_with_restore_history(make_job, starrocks):
    job_id = make_job(job_type="restore", label=None, params_json='{"target_label": "inc_y"}')
    starrocks.state = "FINISHED"

    jobs_commands.reconcile_stale_jobs()

    assert _job(job_id).status == JobStatus.FAILED.value
    with session_module.session_scope() as session:
        events = session.scalars(select(RestoreHistory).where(RestoreHistory.job_id == job_id)).all()
    assert [e.status for e in events] == ["FAILED"]


def test_stale_retention_is_failed_with_retention_history(make_job, starrocks):
    job_id = make_job(job_type="retention", label=None, params_json='{"schedule_id": 1}')

    jobs_commands.reconcile_stale_jobs()

    assert _job(job_id).status == JobStatus.FAILED.value
    with session_module.session_scope() as session:
        events = session.scalars(select(RetentionHistory).where(RetentionHistory.job_id == job_id)).all()
    assert [e.status for e in events] == ["FAILED"]
    starrocks.lookup.assert_not_called()


def test_job_claimed_by_someone_else_is_skipped(make_job, starrocks, mocker):
    job_id = make_job()
    mocker.patch.object(jobs_commands.jobs_dal, "claim_stale_job", return_value=False)

    summary = jobs_commands.reconcile_stale_jobs()

    assert summary.skipped == [job_id]
    assert _job(job_id).status == JobStatus.RUNNING.value
    starrocks.lookup.assert_not_called()


def test_one_jobs_error_does_not_stop_the_pass(make_job, starrocks, mocker):
    first = make_job(job_type="schedule_cleanup", label=None)
    second = make_job(job_type="schedule_cleanup", label=None)
    real_fail = jobs_commands._fail_job
    calls = []

    def flaky(job, *args, **kwargs):
        calls.append(job.id)
        if job.id == first:
            raise RuntimeError("boom")
        return real_fail(job, *args, **kwargs)

    mocker.patch.object(jobs_commands, "_fail_job", side_effect=flaky)

    summary = jobs_commands.reconcile_stale_jobs()

    assert calls == [first, second]
    assert first in summary.skipped
    assert second in summary.failed


class TestFindOperationState:
    def test_matches_a_dict_row_by_label(self, mocker):
        mocker.patch.object(
            reconcile.backup_dal, "show_backup", return_value=[{"SnapshotName": "x", "State": "UPLOADING"}]
        )

        assert reconcile.find_operation_state(MagicMock(), "backup", ["db"], ["x"]) == "UPLOADING"

    def test_matches_a_restore_tuple_row(self, mocker):
        mocker.patch.object(
            reconcile.restore_dal, "show_restore", return_value=[(1, "inc_y", "ts", "db", "FINISHED")]
        )

        assert reconcile.find_operation_state(MagicMock(), "restore", ["db"], ["inc_y"]) == "FINISHED"

    def test_other_label_means_not_found(self, mocker):
        mocker.patch.object(
            reconcile.backup_dal, "show_backup", return_value=[{"SnapshotName": "other", "State": "FINISHED"}]
        )

        assert reconcile.find_operation_state(MagicMock(), "backup", ["db"], ["x"]) is None

    def test_a_failing_database_is_skipped(self, mocker):
        mocker.patch.object(
            reconcile.backup_dal,
            "show_backup",
            side_effect=[RuntimeError("unknown db"), [{"SnapshotName": "x", "State": "CANCELLED"}]],
        )

        assert reconcile.find_operation_state(MagicMock(), "backup", ["gone", "db"], ["x"]) == "CANCELLED"
