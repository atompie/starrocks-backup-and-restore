import datetime

import pytest

from starrocks_br.dal.metadata import jobs as jobs_dal
from starrocks_br.dal.metadata import schedules
from starrocks_br.store.models import Job, JobStatus

NOW = datetime.datetime(2026, 9, 29, 12, 0, 0, tzinfo=datetime.timezone.utc)


@pytest.fixture
def add_job(sqlite_session):
    def _add(cluster_id, job_type="backup_full", status=JobStatus.PENDING.value, schedule_id=None) -> Job:
        job = Job(
            cluster_id=cluster_id,
            job_type=job_type,
            backend="thread",
            params_json="{}",
            status=status,
            schedule_id=schedule_id,
        )
        sqlite_session.add(job)
        sqlite_session.commit()
        return job

    return _add


def test_clusters_with_pending_jobs_lists_each_cluster_once(sqlite_session, make_cluster, add_job):
    first = make_cluster("a")
    second = make_cluster("b")
    idle = make_cluster("c")
    add_job(first.id)
    add_job(first.id)
    add_job(second.id)
    add_job(idle.id, status=JobStatus.SUCCESS.value)

    assert jobs_dal.clusters_with_pending_jobs(sqlite_session) == [first.id, second.id]


def test_cluster_has_running_job_only_for_that_cluster(sqlite_session, make_cluster, add_job):
    busy = make_cluster("busy")
    other = make_cluster("other")
    add_job(busy.id, status=JobStatus.RUNNING.value)
    add_job(other.id, status=JobStatus.PENDING.value)

    assert jobs_dal.cluster_has_running_job(sqlite_session, busy.id) is True
    assert jobs_dal.cluster_has_running_job(sqlite_session, other.id) is False


def test_next_pending_job_orders_restore_then_backup_then_other_then_oldest(
    sqlite_session, make_cluster, add_job
):
    cluster = make_cluster()
    cleanup = add_job(cluster.id, job_type="schedule_cleanup")
    backup_new = add_job(cluster.id, job_type="backup_incremental")
    backup_old = add_job(cluster.id, job_type="backup_full")
    restore = add_job(cluster.id, job_type="restore")
    order = []

    for _ in range(4):
        job = jobs_dal.next_pending_job(sqlite_session, cluster.id)
        order.append(job.id)
        job.status = JobStatus.SUCCESS.value
        sqlite_session.commit()

    assert order == [restore.id, backup_new.id, backup_old.id, cleanup.id]


def test_next_pending_job_is_none_when_nothing_is_pending(sqlite_session, make_cluster, add_job):
    cluster = make_cluster()
    add_job(cluster.id, status=JobStatus.RUNNING.value)

    assert jobs_dal.next_pending_job(sqlite_session, cluster.id) is None


def test_claim_pending_job_succeeds_once_and_stamps_start_and_heartbeat(sqlite_session, make_cluster, add_job):
    job = add_job(make_cluster().id)

    first = jobs_dal.claim_pending_job(sqlite_session, job.id, NOW)
    second = jobs_dal.claim_pending_job(sqlite_session, job.id, NOW)
    sqlite_session.refresh(job)

    assert (first, second) == (True, False)
    assert job.status == JobStatus.RUNNING.value
    assert job.started_at is not None
    assert job.heartbeat_at is not None


def test_schedule_has_open_job_counts_pending_and_running_of_the_same_type_only(
    sqlite_session, make_cluster, make_group, add_job
):
    cluster = make_cluster()
    group_id = make_group(cluster.id)
    schedule = schedules.create(
        sqlite_session,
        cluster_id=cluster.id,
        job_type="backup_full",
        inventory_group_id=group_id,
        repository="repo",
        cadence="0 0 * * *",
        backend="thread",
        enabled=True,
        next_run_at=NOW,
    )
    add_job(cluster.id, job_type="backup_full", status=JobStatus.SUCCESS.value, schedule_id=schedule.id)
    add_job(cluster.id, job_type="retention", status=JobStatus.PENDING.value, schedule_id=schedule.id)

    assert jobs_dal.schedule_has_open_job(sqlite_session, schedule.id, "backup_full") is False

    add_job(cluster.id, job_type="backup_full", status=JobStatus.PENDING.value, schedule_id=schedule.id)
    assert jobs_dal.schedule_has_open_job(sqlite_session, schedule.id, "backup_full") is True
    assert jobs_dal.schedule_has_open_job(sqlite_session, schedule.id + 1, "backup_full") is False
