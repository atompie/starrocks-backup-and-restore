import datetime

import pytest

from starrocks_br.dal.metadata import retention as retention_dal
from starrocks_br.store.models import BackupReference, Job, Schedule

T0 = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)


@pytest.fixture
def setup(sqlite_session, make_cluster, make_group, make_job):
    cluster = make_cluster()
    group_id = make_group(cluster.id)

    def schedule(retention=2, job_type="backup_full", cadence="0 * * * *", deletion=None) -> Schedule:
        row = Schedule(
            cluster_id=cluster.id,
            job_type=job_type,
            inventory_group_id=group_id,
            repository="repo",
            cadence=cadence,
            retention=retention,
            deletion_requested_at=deletion,
        )
        sqlite_session.add(row)
        sqlite_session.commit()
        return row

    def backup(sched, minute, status="SUCCESS", job_type="backup_full", deleted=False, baseline=None) -> Job:
        job = make_job(
            cluster.id, job_type=job_type, status=status, finished_at=T0 + datetime.timedelta(minutes=minute)
        )
        job.schedule_id = sched.id
        job.baseline_job_id = baseline.id if baseline else None
        job.label = f"snap_{job.id}"
        sqlite_session.add(
            BackupReference(
                job_id=job.id,
                repository="repo",
                snapshot_label=job.label,
                snapshot_timestamp=T0,
                database_name="db",
                table_name="t",
                partition_name="p",
                deleted_at=T0 if deleted else None,
            )
        )
        sqlite_session.commit()
        return job

    def restore(source, status="PENDING") -> Job:
        job = make_job(cluster.id, job_type="restore", status=status)
        job.source_backup_job_id = source.id
        sqlite_session.commit()
        return job

    return cluster, schedule, backup, restore


def test_pool_is_newest_first_and_skips_failed_deleted_and_foreign_jobs(sqlite_session, setup):
    _, schedule, backup, _ = setup
    mine, other = schedule(), schedule()
    old = backup(mine, 1)
    new = backup(mine, 3)
    backup(mine, 2, status="FAILED")
    backup(mine, 4, deleted=True)
    backup(mine, 5, job_type="backup_incremental")
    backup(other, 6)

    assert [j.id for j in retention_dal.eligible_full_backups(sqlite_session, mine.id)] == [new.id, old.id]


def test_droppable_keeps_the_newest_retention_count(sqlite_session, setup):
    _, schedule, backup, _ = setup
    sched = schedule(retention=2)
    jobs = [backup(sched, m) for m in range(1, 5)]

    droppable = retention_dal.droppable_backups(sqlite_session, sched)

    assert [j.id for j in droppable] == [jobs[1].id, jobs[0].id]


def test_nothing_is_droppable_within_the_retention_count(sqlite_session, setup):
    _, schedule, backup, _ = setup
    sched = schedule(retention=3)
    for m in range(1, 4):
        backup(sched, m)

    assert retention_dal.droppable_backups(sqlite_session, sched) == []


@pytest.mark.parametrize("status", ["PENDING", "RUNNING", "SUCCESS"])
def test_incremental_baseline_is_protected_even_from_another_schedule(sqlite_session, setup, status):
    _, schedule, backup, _ = setup
    sched, inc_sched = schedule(retention=1), schedule(retention=None, job_type="backup_incremental")
    oldest = backup(sched, 1)
    backup(sched, 2)
    backup(inc_sched, 3, status=status, job_type="backup_incremental", baseline=oldest)

    assert retention_dal.droppable_backups(sqlite_session, sched) == []


def test_failed_incremental_does_not_protect_its_baseline(sqlite_session, setup):
    _, schedule, backup, _ = setup
    sched, inc_sched = schedule(retention=1), schedule(retention=None, job_type="backup_incremental")
    oldest = backup(sched, 1)
    backup(sched, 2)
    backup(inc_sched, 3, status="FAILED", job_type="backup_incremental", baseline=oldest)

    assert [j.id for j in retention_dal.droppable_backups(sqlite_session, sched)] == [oldest.id]


@pytest.mark.parametrize("status,protected", [("PENDING", True), ("RUNNING", True), ("SUCCESS", False), ("FAILED", False)])
def test_open_restore_protects_its_source(sqlite_session, setup, status, protected):
    _, schedule, backup, restore = setup
    sched = schedule(retention=1)
    oldest = backup(sched, 1)
    backup(sched, 2)
    restore(oldest, status=status)

    droppable = retention_dal.droppable_backups(sqlite_session, sched)

    assert (droppable == []) is protected


def test_open_restore_protects_the_baseline_of_its_source(sqlite_session, setup):
    _, schedule, backup, restore = setup
    sched, inc_sched = schedule(retention=1), schedule(retention=None, job_type="backup_incremental")
    oldest = backup(sched, 1)
    backup(sched, 2)
    incremental = backup(inc_sched, 3, status="FAILED", job_type="backup_incremental", baseline=oldest)
    restore(incremental)

    assert retention_dal.droppable_backups(sqlite_session, sched) == []


def test_sweep_selects_only_recurring_full_schedules_not_pending_deletion(sqlite_session, setup):
    _, schedule, _, _ = setup
    wanted = schedule()
    schedule(cadence=None, retention=None)
    schedule(job_type="backup_incremental", retention=None)
    schedule(deletion=T0)

    assert [s.id for s in retention_dal.schedules_subject_to_retention(sqlite_session)] == [wanted.id]


def test_mark_references_deleted_hides_the_backup_from_the_pool_but_keeps_the_job(sqlite_session, setup):
    _, schedule, backup, _ = setup
    sched = schedule()
    job = backup(sched, 1)

    assert retention_dal.live_snapshots_for_job(sqlite_session, job.id) == [("repo", job.label)]
    assert retention_dal.mark_references_deleted(sqlite_session, job.id, T0) == 1

    assert retention_dal.live_snapshots_for_job(sqlite_session, job.id) == []
    assert retention_dal.eligible_full_backups(sqlite_session, sched.id) == []
    assert sqlite_session.get(Job, job.id) is not None
    assert retention_dal.mark_references_deleted(sqlite_session, job.id, T0) == 0
