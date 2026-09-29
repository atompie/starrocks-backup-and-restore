import datetime

import pytest

from starrocks_br import exceptions
from starrocks_br.commands.restore import submit_restore_job
from starrocks_br.commands.schedules import (
    delete_schedule,
    expire_due_schedules,
    run_schedule_cleanup,
    update_schedule,
)
from starrocks_br.store.models import (
    Base,
    BackupReference,
    Cluster,
    InventoryGroup,
    Job,
    JobStatus,
    Schedule,
)
from starrocks_br.store.session import get_engine, session_scope


@pytest.fixture
def sqlite_store(tmp_path, monkeypatch):
    monkeypatch.setenv("STARROCKS_BR_DATABASE_URL", f"sqlite:///{tmp_path / 'schedule_cleanup.db'}")
    from starrocks_br.store import session as session_module

    session_module.reset_engine_cache()
    Base.metadata.create_all(get_engine())
    yield
    session_module.reset_engine_cache()


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _make_cluster(session) -> Cluster:
    cluster = Cluster(name="c1", host="h", port=9030, user="u", password_encrypted="enc")
    session.add(cluster)
    session.flush()
    return cluster


def _make_group(session, cluster: Cluster, name: str = "g1") -> InventoryGroup:
    group = InventoryGroup(cluster_id=cluster.id, name=name)
    session.add(group)
    session.flush()
    return group


def _make_schedule(session, cluster: Cluster, group: InventoryGroup, **overrides) -> Schedule:
    fields = dict(
        cluster_id=cluster.id,
        job_type="backup_full",
        inventory_group_id=group.id,
        repository="repo",
        cadence="0 1 * * *",
        backend="thread",
        enabled=True,
        retention=3,
        next_run_at=_utcnow() + datetime.timedelta(days=1),
    )
    fields.update(overrides)
    schedule = Schedule(**fields)
    session.add(schedule)
    session.flush()
    return schedule


def _make_backup_job(session, cluster: Cluster, schedule: Schedule, status=JobStatus.SUCCESS.value, **overrides) -> Job:
    fields = dict(
        cluster_id=cluster.id,
        job_type="backup_full",
        backend="thread",
        params_json="{}",
        schedule_id=schedule.id,
        status=status,
        label="label1",
        repository="repo",
    )
    fields.update(overrides)
    job = Job(**fields)
    session.add(job)
    session.flush()
    return job


class TestDeleteScheduleValidation:
    def test_accepted_deletion_marks_schedule_and_creates_cleanup_job(self, sqlite_store, mocker):
        with session_scope() as session:
            cluster = _make_cluster(session)
            group = _make_group(session, cluster)
            schedule = _make_schedule(session, cluster, group)

            cleanup_job = Job(cluster_id=cluster.id, job_type="schedule_cleanup", backend="thread", params_json="{}")
            mocker.patch("starrocks_br.commands.schedules.submit_job", return_value=cleanup_job)

            job = delete_schedule(session, schedule)

            assert job is cleanup_job
            assert schedule.deletion_requested_at is not None

    def test_active_backup_job_blocks_deletion(self, sqlite_store):
        with session_scope() as session:
            cluster = _make_cluster(session)
            group = _make_group(session, cluster)
            schedule = _make_schedule(session, cluster, group)
            _make_backup_job(session, cluster, schedule, status=JobStatus.RUNNING.value)

            with pytest.raises(exceptions.ScheduleHasActiveJobError):
                delete_schedule(session, schedule)

            assert schedule.deletion_requested_at is None

    def test_active_restore_blocks_deletion(self, sqlite_store):
        with session_scope() as session:
            cluster = _make_cluster(session)
            group = _make_group(session, cluster)
            schedule = _make_schedule(session, cluster, group)
            backup_job = _make_backup_job(session, cluster, schedule)
            session.add(
                Job(
                    cluster_id=cluster.id,
                    job_type="restore",
                    backend="thread",
                    params_json="{}",
                    status=JobStatus.PENDING.value,
                    source_backup_job_id=backup_job.id,
                )
            )
            session.flush()

            with pytest.raises(exceptions.ScheduleHasActiveRestoreError):
                delete_schedule(session, schedule)

            assert schedule.deletion_requested_at is None

    def test_incremental_baseline_dependency_blocks_deletion(self, sqlite_store):
        with session_scope() as session:
            cluster = _make_cluster(session)
            group = _make_group(session, cluster)
            schedule = _make_schedule(session, cluster, group)
            other_schedule = _make_schedule(session, cluster, group, cadence="0 2 * * *")
            full_job = _make_backup_job(session, cluster, schedule)
            session.add(
                Job(
                    cluster_id=cluster.id,
                    job_type="backup_incremental",
                    backend="thread",
                    params_json="{}",
                    status=JobStatus.SUCCESS.value,
                    schedule_id=other_schedule.id,
                    baseline_job_id=full_job.id,
                )
            )
            session.flush()

            with pytest.raises(exceptions.ScheduleHasIncrementalBaselineError):
                delete_schedule(session, schedule)

            assert schedule.deletion_requested_at is None

    def test_repeated_deletion_returns_existing_active_cleanup_job(self, sqlite_store, mocker):
        with session_scope() as session:
            cluster = _make_cluster(session)
            group = _make_group(session, cluster)
            schedule = _make_schedule(session, cluster, group)

            def _fake_submit_job(db, cluster, job_type, params, backend, schedule_id=None):
                job = Job(
                    cluster_id=cluster.id,
                    job_type=job_type,
                    backend=backend or "thread",
                    params_json="{}",
                    schedule_id=schedule_id,
                    status=JobStatus.PENDING.value,
                )
                db.add(job)
                db.flush()
                return job

            submit_job = mocker.patch(
                "starrocks_br.commands.schedules.submit_job", side_effect=_fake_submit_job
            )

            first = delete_schedule(session, schedule)
            second = delete_schedule(session, schedule)

            assert first.id == second.id
            submit_job.assert_called_once()

    def test_patch_is_rejected_once_pending_deletion(self, sqlite_store, mocker):
        with session_scope() as session:
            cluster = _make_cluster(session)
            group = _make_group(session, cluster)
            schedule = _make_schedule(session, cluster, group)

            cleanup_job = Job(cluster_id=cluster.id, job_type="schedule_cleanup", backend="thread", params_json="{}")
            mocker.patch("starrocks_br.commands.schedules.submit_job", return_value=cleanup_job)
            delete_schedule(session, schedule)

            with pytest.raises(exceptions.SchedulePendingDeletionError):
                update_schedule(session, schedule, {"enabled": False})


class _StubBackend:
    name = "thread"

    def enqueue(self, job_id: int) -> None:
        pass


class TestRestoreSubmissionCoordination:
    def test_restore_submission_blocked_once_source_schedule_pending_deletion(self, sqlite_store):
        from starrocks_br.jobs.backend import BackendRegistry, reset_registry, set_registry

        with session_scope() as session:
            cluster = _make_cluster(session)
            group = _make_group(session, cluster)
            schedule = _make_schedule(session, cluster, group)
            backup_job = _make_backup_job(session, cluster, schedule)
            schedule.deletion_requested_at = _utcnow()
            session.flush()

            set_registry(BackendRegistry({"thread": _StubBackend()}, "thread"))
            try:
                with pytest.raises(exceptions.RestoreSourcePendingDeletionError):
                    submit_restore_job(session, cluster, {"target_label": backup_job.label}, "thread")
            finally:
                reset_registry()

    def test_restore_submission_succeeds_and_records_source_backup_job_id(self, sqlite_store):
        from starrocks_br.jobs.backend import BackendRegistry, reset_registry, set_registry

        with session_scope() as session:
            cluster = _make_cluster(session)
            group = _make_group(session, cluster)
            schedule = _make_schedule(session, cluster, group)
            backup_job = _make_backup_job(session, cluster, schedule)
            session.flush()

            set_registry(BackendRegistry({"thread": _StubBackend()}, "thread"))
            try:
                job = submit_restore_job(session, cluster, {"target_label": backup_job.label}, "thread")
            finally:
                reset_registry()

            assert job.source_backup_job_id == backup_job.id


class TestRunScheduleCleanup:
    def test_cleanup_with_no_references_deletes_backup_jobs_and_schedule(self, sqlite_store):
        with session_scope() as session:
            cluster = _make_cluster(session)
            group = _make_group(session, cluster)
            schedule = _make_schedule(session, cluster, group)
            failed_job = _make_backup_job(session, cluster, schedule, status=JobStatus.FAILED.value, label=None)
            schedule.deletion_requested_at = _utcnow()
            schedule.last_run_job_id = failed_job.id
            session.flush()
            schedule_id = schedule.id
            cluster_obj = cluster

        result = run_schedule_cleanup(cluster_obj, {"schedule_id": schedule_id}, job_id=1)

        assert result == {"schedule_id": schedule_id, "snapshots_dropped": 0}
        with session_scope() as session:
            assert session.get(Schedule, schedule_id) is None
            assert session.get(Job, failed_job.id) is None

    def test_cleanup_drops_each_distinct_snapshot_once(self, sqlite_store, mocker):
        with session_scope() as session:
            cluster = _make_cluster(session)
            group = _make_group(session, cluster)
            schedule = _make_schedule(session, cluster, group)
            backup_job = _make_backup_job(session, cluster, schedule)
            for table in ("orders", "customers"):
                session.add(
                    BackupReference(
                        job_id=backup_job.id,
                        repository="repo",
                        snapshot_label="label1",
                        snapshot_timestamp=_utcnow(),
                        database_name="sales_db",
                        table_name=table,
                    )
                )
            schedule.deletion_requested_at = _utcnow()
            session.flush()
            schedule_id = schedule.id
            cluster_obj = cluster

        mocker.patch("starrocks_br.commands.schedules.connect", return_value=mocker.MagicMock(__enter__=mocker.Mock(return_value=mocker.Mock()), __exit__=mocker.Mock(return_value=False)))
        mocker.patch("starrocks_br.commands.schedules.ensure_ready")
        mocker.patch("starrocks_br.commands.schedules.prune.verify_snapshot_exists", return_value=True)
        drop_mock = mocker.patch("starrocks_br.commands.schedules.prune.execute_drop_snapshot")

        result = run_schedule_cleanup(cluster_obj, {"schedule_id": schedule_id}, job_id=1)

        assert result["snapshots_dropped"] == 1
        drop_mock.assert_called_once_with(mocker.ANY, "repo", "label1")
        with session_scope() as session:
            assert session.get(Schedule, schedule_id) is None

    def test_cleanup_retry_treats_already_absent_snapshot_as_dropped(self, sqlite_store, mocker):
        with session_scope() as session:
            cluster = _make_cluster(session)
            group = _make_group(session, cluster)
            schedule = _make_schedule(session, cluster, group)
            backup_job = _make_backup_job(session, cluster, schedule)
            session.add(
                BackupReference(
                    job_id=backup_job.id,
                    repository="repo",
                    snapshot_label="label1",
                    snapshot_timestamp=_utcnow(),
                    database_name="sales_db",
                    table_name="orders",
                )
            )
            schedule.deletion_requested_at = _utcnow()
            session.flush()
            schedule_id = schedule.id
            cluster_obj = cluster

        mocker.patch("starrocks_br.commands.schedules.connect", return_value=mocker.MagicMock(__enter__=mocker.Mock(return_value=mocker.Mock()), __exit__=mocker.Mock(return_value=False)))
        mocker.patch("starrocks_br.commands.schedules.ensure_ready")
        mocker.patch("starrocks_br.commands.schedules.prune.verify_snapshot_exists", side_effect=Exception("not found"))
        drop_mock = mocker.patch("starrocks_br.commands.schedules.prune.execute_drop_snapshot")

        result = run_schedule_cleanup(cluster_obj, {"schedule_id": schedule_id}, job_id=1)

        drop_mock.assert_not_called()
        assert result["snapshots_dropped"] == 1
        with session_scope() as session:
            assert session.get(Schedule, schedule_id) is None

    def test_deleting_backup_jobs_cascades_dependent_restore_and_history(self, sqlite_store):
        from starrocks_br.store.models import BackupHistory

        with session_scope() as session:
            cluster = _make_cluster(session)
            group = _make_group(session, cluster)
            schedule = _make_schedule(session, cluster, group)
            backup_job = _make_backup_job(session, cluster, schedule)
            session.add(BackupHistory(job_id=backup_job.id, status="FINISHED"))
            restore_job = Job(
                cluster_id=cluster.id,
                job_type="restore",
                backend="thread",
                params_json="{}",
                status=JobStatus.SUCCESS.value,
                source_backup_job_id=backup_job.id,
            )
            session.add(restore_job)
            schedule.deletion_requested_at = _utcnow()
            session.flush()
            schedule_id = schedule.id
            backup_job_id = backup_job.id
            restore_job_id = restore_job.id
            cluster_obj = cluster

        run_schedule_cleanup(cluster_obj, {"schedule_id": schedule_id}, job_id=1)

        with session_scope() as session:
            assert session.get(Job, backup_job_id) is None
            assert session.get(Job, restore_job_id) is None
            assert (
                session.query(BackupHistory).filter(BackupHistory.job_id == backup_job_id).count() == 0
            )


class TestExpireDueSchedules:
    def test_expired_one_shot_schedule_is_cleaned_up(self, sqlite_store, mocker):
        with session_scope() as session:
            cluster = _make_cluster(session)
            group = _make_group(session, cluster)
            schedule = _make_schedule(
                session,
                cluster,
                group,
                cadence=None,
                next_run_at=None,
                retention=None,
                expire_after_days=1,
                created_at=_utcnow() - datetime.timedelta(days=2),
            )

            cleanup_job = Job(cluster_id=cluster.id, job_type="schedule_cleanup", backend="thread", params_json="{}")
            mocker.patch("starrocks_br.commands.schedules.submit_job", return_value=cleanup_job)

            cleanup_job_ids = expire_due_schedules(session, _utcnow())

            assert cleanup_job_ids == [cleanup_job.id]
            assert schedule.deletion_requested_at is not None

    def test_never_expiring_schedule_is_left_alone(self, sqlite_store, mocker):
        with session_scope() as session:
            cluster = _make_cluster(session)
            group = _make_group(session, cluster)
            _make_schedule(
                session,
                cluster,
                group,
                cadence=None,
                next_run_at=None,
                retention=None,
                expire_after_days=None,
                created_at=_utcnow() - datetime.timedelta(days=365),
            )
            submit_job = mocker.patch("starrocks_br.commands.schedules.submit_job")

            cleanup_job_ids = expire_due_schedules(session, _utcnow())

            assert cleanup_job_ids == []
            submit_job.assert_not_called()

    def test_blocked_expiry_is_logged_and_schedule_left_in_place(self, sqlite_store, mocker):
        with session_scope() as session:
            cluster = _make_cluster(session)
            group = _make_group(session, cluster)
            schedule = _make_schedule(
                session,
                cluster,
                group,
                cadence=None,
                next_run_at=None,
                retention=None,
                expire_after_days=1,
                created_at=_utcnow() - datetime.timedelta(days=2),
            )
            _make_backup_job(session, cluster, schedule, status=JobStatus.RUNNING.value)

            cleanup_job_ids = expire_due_schedules(session, _utcnow())

            assert cleanup_job_ids == []
            assert schedule.deletion_requested_at is None

    def test_failed_expiry_cleanup_is_retried_on_a_later_tick(self, sqlite_store, mocker):
        with session_scope() as session:
            cluster = _make_cluster(session)
            group = _make_group(session, cluster)
            schedule = _make_schedule(
                session,
                cluster,
                group,
                cadence=None,
                next_run_at=None,
                retention=None,
                expire_after_days=1,
                created_at=_utcnow() - datetime.timedelta(days=2),
                deletion_requested_at=_utcnow() - datetime.timedelta(minutes=5),
            )
            # No active cleanup job exists (the prior one FAILED) - a later tick retries.
            cleanup_job = Job(cluster_id=cluster.id, job_type="schedule_cleanup", backend="thread", params_json="{}")
            mocker.patch("starrocks_br.commands.schedules.submit_job", return_value=cleanup_job)

            cleanup_job_ids = expire_due_schedules(session, _utcnow())

            assert cleanup_job_ids == [cleanup_job.id]
