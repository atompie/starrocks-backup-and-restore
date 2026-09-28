from starrocks_br.dal.metadata import jobs
from starrocks_br.store.models import BackupHistory, JobStatus, RestoreHistory


def test_get_returns_none_when_missing(sqlite_session):
    assert jobs.get(sqlite_session, 999) is None


def test_get_returns_job(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    job = make_job(cluster.id)

    assert jobs.get(sqlite_session, job.id).id == job.id


def test_set_label(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    job = make_job(cluster.id)

    jobs.set_label(sqlite_session, job.id, "full-2026-01-01")

    assert jobs.get(sqlite_session, job.id).label == "full-2026-01-01"


def test_mark_running_sets_status_and_started_at(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    job = make_job(cluster.id, status=JobStatus.PENDING.value)

    updated = jobs.mark_running(sqlite_session, job.id)

    assert updated.status == JobStatus.RUNNING.value
    assert updated.started_at is not None


def test_mark_progress_updates_state_and_pct(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    job = make_job(cluster.id)

    jobs.mark_progress(sqlite_session, job.id, "UPLOADING", 42)

    updated = jobs.get(sqlite_session, job.id)
    assert updated.state_detail == "UPLOADING"
    assert updated.progress_pct == 42


def test_mark_progress_leaves_pct_unchanged_when_none(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    job = make_job(cluster.id)
    jobs.mark_progress(sqlite_session, job.id, "UPLOADING", 42)

    jobs.mark_progress(sqlite_session, job.id, "FINALIZING", None)

    updated = jobs.get(sqlite_session, job.id)
    assert updated.state_detail == "FINALIZING"
    assert updated.progress_pct == 42


def test_mark_failed_sets_status_and_error(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    job = make_job(cluster.id, status=JobStatus.RUNNING.value)

    jobs.mark_failed(sqlite_session, job.id, "boom")

    updated = jobs.get(sqlite_session, job.id)
    assert updated.status == JobStatus.FAILED.value
    assert updated.error_message == "boom"
    assert updated.finished_at is not None


def test_mark_success_sets_status_and_result(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    job = make_job(cluster.id, status=JobStatus.RUNNING.value)

    jobs.mark_success(sqlite_session, job.id, '{"ok": true}')

    updated = jobs.get(sqlite_session, job.id)
    assert updated.status == JobStatus.SUCCESS.value
    assert updated.result_json == '{"ok": true}'
    assert updated.finished_at is not None


def test_list_history_for_job_returns_backup_history_oldest_first(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    job = make_job(cluster.id, job_type="backup_full")
    sqlite_session.add(BackupHistory(job_id=job.id, status="SNAPSHOTING"))
    sqlite_session.add(BackupHistory(job_id=job.id, status="SUCCESS"))
    sqlite_session.commit()

    result = jobs.list_history_for_job(sqlite_session, "backup_full", job.id)

    assert [row.status for row in result] == ["SNAPSHOTING", "SUCCESS"]


def test_list_history_for_job_returns_restore_history(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    job = make_job(cluster.id, job_type="restore")
    sqlite_session.add(RestoreHistory(job_id=job.id, status="DOWNLOADING"))
    sqlite_session.commit()

    result = jobs.list_history_for_job(sqlite_session, "restore", job.id)

    assert [row.status for row in result] == ["DOWNLOADING"]


def test_list_history_for_job_returns_empty_for_unlogged_job_type(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    job = make_job(cluster.id, job_type="prune")

    assert jobs.list_history_for_job(sqlite_session, "prune", job.id) == []
