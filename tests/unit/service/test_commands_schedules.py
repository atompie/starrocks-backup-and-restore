import datetime

import pytest

from starrocks_br import exceptions
from starrocks_br.commands.schedules import _validate_schedule_shape, compute_next_run_at, run_due_schedules
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


class TestValidateScheduleShape:
    def test_recurring_full_requires_retention(self):
        with pytest.raises(exceptions.InvalidScheduleFieldsError, match="retention"):
            _validate_schedule_shape("backup_full", "0 0 * * *", None, None)

    def test_recurring_full_with_retention_is_valid(self):
        _validate_schedule_shape("backup_full", "0 0 * * *", 5, None)

    def test_recurring_full_forbids_expire_after_days(self):
        with pytest.raises(exceptions.InvalidScheduleFieldsError, match="expire_after_days"):
            _validate_schedule_shape("backup_full", "0 0 * * *", 5, 3)

    def test_recurring_incremental_forbids_retention(self):
        with pytest.raises(exceptions.InvalidScheduleFieldsError, match="retention"):
            _validate_schedule_shape("backup_incremental", "0 0 * * *", 5, None)

    def test_recurring_incremental_with_no_retention_is_valid(self):
        _validate_schedule_shape("backup_incremental", "0 0 * * *", None, None)

    def test_one_shot_full_forbids_retention(self):
        with pytest.raises(exceptions.InvalidScheduleFieldsError, match="retention"):
            _validate_schedule_shape("backup_full", None, 5, None)

    def test_one_shot_full_with_no_expiry_is_valid(self):
        _validate_schedule_shape("backup_full", None, None, None)

    def test_one_shot_full_with_expiry_is_valid(self):
        _validate_schedule_shape("backup_full", None, None, 7)

    def test_one_shot_incremental_rejected(self):
        with pytest.raises(exceptions.InvalidScheduleFieldsError, match="incremental"):
            _validate_schedule_shape("backup_incremental", None, None, None)


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
        assert submit_job.call_args.kwargs["schedule_id"] == schedule.id


def test_run_due_schedules_triggers_due_incremental_schedule_with_equivalent_context(
    sqlite_store, mocker
):
    """Mirrors the backup_full case above for backup_incremental - per
    unify-backup-command-entrypoints, both job types must reach `submit_job`
    with the same shape of context (group id and repository)."""
    with session_scope() as session:
        cluster = _make_cluster(session)
        group = _make_group(session, cluster)
        job = Job(cluster_id=cluster.id, job_type="backup_incremental", backend="thread", params_json="{}")
        session.add(job)
        session.flush()
        submit_job = mocker.patch("starrocks_br.commands.schedules.submit_job", return_value=job)

        schedule = Schedule(
            cluster_id=cluster.id,
            job_type="backup_incremental",
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
        submit_job.assert_called_once()
        args = submit_job.call_args.args
        assert args[1].id == cluster.id
        assert args[2] == "backup_incremental"
        assert args[3] == {"group_id": group.id, "repository": "repo"}
        assert args[4] == "thread"
        assert submit_job.call_args.kwargs["schedule_id"] == schedule.id


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


class TestSchedulerLock:
    def test_free_lock_is_acquired(self, sqlite_store):
        from starrocks_br.commands import schedules as commands

        result = commands.try_acquire_scheduler_lock("host:1")

        assert result.acquired is True
        assert result.recovered_stale is False

    def test_active_lock_rejects_a_second_holder(self, sqlite_store):
        from starrocks_br.commands import schedules as commands

        commands.try_acquire_scheduler_lock("host:1")

        assert commands.try_acquire_scheduler_lock("host:2").acquired is False

    def test_expired_lock_is_reclaimed_with_a_warning(self, sqlite_store, monkeypatch, mocker):
        from starrocks_br.commands import schedules as commands

        monkeypatch.setenv("STARROCKS_BR_SCHEDULER_LOCK_TIMEOUT_SECONDS", "1")
        commands.try_acquire_scheduler_lock("host:1")
        later = _utcnow() + datetime.timedelta(seconds=5)
        mocker.patch.object(commands, "_utcnow", return_value=later)
        warning = mocker.patch.object(commands.logger, "warning")

        result = commands.try_acquire_scheduler_lock("host:2")

        assert result.acquired is True
        assert result.recovered_stale is True
        assert "stale" in warning.call_args.args[0].lower()

    def test_release_lets_the_next_holder_in_without_a_stale_warning(self, sqlite_store, mocker):
        from starrocks_br.commands import schedules as commands

        commands.try_acquire_scheduler_lock("host:1")
        assert commands.release_scheduler_lock("host:1") is True
        warning = mocker.patch.object(commands.logger, "warning")

        result = commands.try_acquire_scheduler_lock("host:2")

        assert result.acquired is True
        warning.assert_not_called()

    def test_release_by_a_non_holder_does_not_clear_the_lock(self, sqlite_store):
        from starrocks_br.commands import schedules as commands

        commands.try_acquire_scheduler_lock("host:2")

        assert commands.release_scheduler_lock("host:1") is False
        assert commands.try_acquire_scheduler_lock("host:3").acquired is False

    def test_holder_id_is_hostname_and_pid(self):
        import os
        import socket

        from starrocks_br.commands import schedules as commands

        assert commands.scheduler_holder_id() == f"{socket.gethostname()}:{os.getpid()}"


class TestExecuteSchedulerTick:
    @pytest.fixture
    def steps(self, mocker):
        from starrocks_br.commands import schedules as commands

        calls = []
        mocker.patch.object(
            commands, "reconcile_stale_jobs", side_effect=lambda: calls.append("reconcile") or "summary"
        )
        mocker.patch.object(
            commands, "run_due_schedules", side_effect=lambda s, n: calls.append("run_due") or ([7], 1)
        )
        mocker.patch.object(
            commands, "expire_due_schedules", side_effect=lambda s, n: calls.append("expire") or [9]
        )
        return calls

    def test_runs_reconcile_then_due_then_expiry_and_records_the_tick(self, sqlite_store, steps):
        from starrocks_br.commands import schedules as commands

        result = commands.execute_scheduler_tick("host:1")

        assert steps == ["reconcile", "run_due", "expire"]
        assert result.acquired is True
        assert result.triggered_job_ids == [7]
        assert result.cleanup_job_ids == [9]
        with session_scope() as session:
            assert commands.get_scheduler_last_tick_at(session) is not None

    def test_releases_the_lock_after_a_successful_tick(self, sqlite_store, steps):
        from starrocks_br.commands import schedules as commands

        commands.execute_scheduler_tick("host:1")

        assert commands.try_acquire_scheduler_lock("host:2").acquired is True

    def test_lock_contention_does_nothing_and_records_no_tick(self, sqlite_store, steps):
        from starrocks_br.commands import schedules as commands

        commands.try_acquire_scheduler_lock("host:1")

        result = commands.execute_scheduler_tick("host:2")

        assert result.acquired is False
        assert steps == []
        with session_scope() as session:
            assert commands.get_scheduler_last_tick_at(session) is None

    def test_failure_releases_the_lock_and_records_no_tick(self, sqlite_store, steps, mocker):
        from starrocks_br.commands import schedules as commands

        mocker.patch.object(commands, "run_due_schedules", side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError, match="boom"):
            commands.execute_scheduler_tick("host:1")

        assert steps == ["reconcile"]
        with session_scope() as session:
            assert commands.get_scheduler_last_tick_at(session) is None
        assert commands.try_acquire_scheduler_lock("host:2").acquired is True

    def test_reports_a_recovered_stale_lock(self, sqlite_store, steps, mocker):
        from starrocks_br.commands import schedules as commands

        mocker.patch.object(
            commands,
            "try_acquire_scheduler_lock",
            return_value=commands.SchedulerLockResult(acquired=True, recovered_stale=True),
        )

        assert commands.execute_scheduler_tick("host:1").recovered_stale_lock is True
