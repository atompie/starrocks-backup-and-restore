import datetime
from unittest.mock import MagicMock

import pytest

from starrocks_br.commands import retention as retention_commands
from starrocks_br.commands.retention import run_retention, submit_due_retention_jobs
from starrocks_br.store import session as session_module
from starrocks_br.store.models import (
    BackupReference,
    Base,
    Cluster,
    InventoryGroup,
    Job,
    RetentionHistory,
    Schedule,
)
from starrocks_br.store.session import session_scope

T0 = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("STARROCKS_BR_DATABASE_URL", f"sqlite:///{tmp_path / 'retention.db'}")
    session_module.reset_engine_cache()
    Base.metadata.create_all(session_module.get_engine())
    yield
    session_module.reset_engine_cache()


class World:
    """Builds a cluster with schedules and backups in the real (temp SQLite) store."""

    def __init__(self):
        with session_scope() as session:
            cluster = Cluster(name="c1", host="h", port=9030, user="u", password_encrypted="enc")
            session.add(cluster)
            session.flush()
            group = InventoryGroup(cluster_id=cluster.id, name="g")
            session.add(group)
            session.flush()
            self.cluster_id, self.group_id = cluster.id, group.id
        self.minute = 0

    def schedule(self, retention=2, job_type="backup_full", cadence="0 * * * *") -> int:
        with session_scope() as session:
            row = Schedule(
                cluster_id=self.cluster_id,
                job_type=job_type,
                inventory_group_id=self.group_id,
                repository="repo",
                cadence=cadence,
                backend="thread",
                retention=retention,
            )
            session.add(row)
            session.flush()
            return row.id

    def job(self, schedule_id, status="SUCCESS", job_type="backup_full", baseline=None, source=None, refs=True) -> int:
        self.minute += 1
        with session_scope() as session:
            job = Job(
                cluster_id=self.cluster_id,
                job_type=job_type,
                backend="thread",
                params_json="{}",
                status=status,
                schedule_id=schedule_id,
                baseline_job_id=baseline,
                source_backup_job_id=source,
                finished_at=T0 + datetime.timedelta(minutes=self.minute),
            )
            session.add(job)
            session.flush()
            job.label = f"snap_{job.id}"
            if refs and job_type.startswith("backup"):
                session.add(
                    BackupReference(
                        job_id=job.id,
                        repository="repo",
                        snapshot_label=job.label,
                        snapshot_timestamp=T0,
                        database_name="db",
                        table_name="t",
                        partition_name="p",
                    )
                )
            return job.id

    def cluster(self) -> Cluster:
        with session_scope() as session:
            cluster = session.get(Cluster, self.cluster_id)
            session.expunge(cluster)
            return cluster


@pytest.fixture
def world(store):
    return World()


@pytest.fixture
def starrocks(mocker):
    """Patch out StarRocks: every snapshot is present unless a test changes `present`."""
    database = MagicMock()
    database.__enter__.return_value = database
    mocker.patch.object(retention_commands, "connect", return_value=database)
    mocker.patch.object(retention_commands, "ensure_ready")
    present = mocker.patch.object(retention_commands.prune_db, "snapshot_present", return_value=True)
    drop = mocker.patch.object(retention_commands.prune_db, "execute_drop_snapshot")
    return MagicMock(present=present, drop=drop)


def _retention_job(world, schedule_id, status="PENDING") -> int:
    return world.job(schedule_id, status=status, job_type="retention", refs=False)


def _live_backup_ids(schedule_id) -> set[int]:
    with session_scope() as session:
        rows = session.query(BackupReference.job_id).filter_by(deleted_at=None).distinct().all()
        return {r[0] for r in rows}


def _events(job_id) -> list[str]:
    with session_scope() as session:
        rows = session.query(RetentionHistory).filter_by(job_id=job_id).order_by(RetentionHistory.id).all()
        return [r.status for r in rows]


def _run(world, schedule_id, job_id):
    return run_retention(world.cluster(), {"schedule_id": schedule_id}, job_id)


class TestRunRetention:
    def test_drops_the_oldest_backups_beyond_retention_and_keeps_their_jobs(self, world, starrocks):
        sched = world.schedule(retention=2)
        jobs = [world.job(sched) for _ in range(4)]
        retention_job = _retention_job(world, sched)

        result = _run(world, sched, retention_job)

        assert result["dropped"] == [jobs[1], jobs[0]]
        assert _live_backup_ids(sched) == {jobs[2], jobs[3]}
        assert {c.args[2] for c in starrocks.drop.call_args_list} == {f"snap_{jobs[0]}", f"snap_{jobs[1]}"}
        with session_scope() as session:
            assert all(session.get(Job, j) is not None for j in jobs)
        assert _events(retention_job) == [
            "RETENTION_STARTED",
            "SNAPSHOT_DROPPED",
            "SNAPSHOT_DROPPED",
            "RETENTION_FINISHED",
        ]

    @pytest.mark.usefixtures("starrocks")
    def test_spec_example_keeps_newest_five_successes_and_ignores_failures(self, world):
        sched = world.schedule(retention=5)
        statuses = ["SUCCESS", "SUCCESS", "FAILED", "SUCCESS", "FAILED", "SUCCESS", "SUCCESS", "SUCCESS"]
        jobs = [world.job(sched, status=s, refs=(s == "SUCCESS")) for s in statuses]

        result = _run(world, sched, _retention_job(world, sched))

        assert result["dropped"] == [jobs[0]]

    @pytest.mark.usefixtures("starrocks")
    def test_spec_example_with_an_incremental_on_job_one_drops_nothing_until_it_is_gone(self, world):
        sched = world.schedule(retention=5)
        inc_sched = world.schedule(retention=None, job_type="backup_incremental")
        statuses = ["SUCCESS", "SUCCESS", "FAILED", "SUCCESS", "FAILED", "SUCCESS", "SUCCESS", "SUCCESS"]
        jobs = [world.job(sched, status=s, refs=(s == "SUCCESS")) for s in statuses]
        incremental = world.job(inc_sched, job_type="backup_incremental", baseline=jobs[0])

        assert _run(world, sched, _retention_job(world, sched))["dropped"] == []

        with session_scope() as session:
            session.delete(session.get(Job, incremental))
        assert _run(world, sched, _retention_job(world, sched))["dropped"] == [jobs[0]]

    def test_baseline_of_an_incremental_is_not_dropped(self, world, starrocks):
        sched = world.schedule(retention=1)
        inc_sched = world.schedule(retention=None, job_type="backup_incremental")
        oldest = world.job(sched)
        world.job(sched)
        world.job(inc_sched, job_type="backup_incremental", baseline=oldest)

        result = _run(world, sched, _retention_job(world, sched))

        assert result["dropped"] == []
        starrocks.drop.assert_not_called()

    @pytest.mark.usefixtures("starrocks")
    def test_source_of_a_queued_restore_is_not_dropped(self, world):
        sched = world.schedule(retention=1)
        oldest = world.job(sched)
        world.job(sched)
        world.job(None, status="PENDING", job_type="restore", source=oldest, refs=False)

        result = _run(world, sched, _retention_job(world, sched))

        assert result["dropped"] == []

    def test_restore_queued_after_selection_protects_the_backup(self, world, starrocks):
        sched = world.schedule(retention=1)
        first, second = world.job(sched), world.job(sched)
        world.job(sched)

        def restore_queued_for_first_drop(*args):
            if not world_state["queued"]:
                world_state["queued"] = True
                world.job(None, status="PENDING", job_type="restore", source=first, refs=False)
            return True

        world_state = {"queued": False}
        starrocks.present.side_effect = restore_queued_for_first_drop

        result = _run(world, sched, _retention_job(world, sched))

        assert result["dropped"] == [second]
        assert result["skipped_protected"] == [first]
        assert first in _live_backup_ids(sched)

    @pytest.mark.usefixtures("starrocks")
    def test_independent_schedules_do_not_touch_each_others_backups(self, world):
        mine, other = world.schedule(retention=1), world.schedule(retention=1)
        for _ in range(2):
            world.job(mine)
        other_jobs = [world.job(other) for _ in range(3)]

        _run(world, mine, _retention_job(world, mine))

        assert set(other_jobs) <= _live_backup_ids(other)

    def test_absent_snapshot_counts_as_dropped(self, world, starrocks):
        sched = world.schedule(retention=1)
        oldest = world.job(sched)
        world.job(sched)
        starrocks.present.return_value = False

        result = _run(world, sched, _retention_job(world, sched))

        assert result["dropped"] == [oldest]
        starrocks.drop.assert_not_called()
        assert oldest not in _live_backup_ids(sched)

    def test_drop_failure_fails_the_job_stops_the_run_and_leaves_backup_jobs_untouched(self, world, starrocks):
        sched = world.schedule(retention=1)
        jobs = [world.job(sched) for _ in range(4)]
        retention_job = _retention_job(world, sched)
        starrocks.drop.side_effect = RuntimeError("s3 unreachable")

        with pytest.raises(RuntimeError, match="s3 unreachable"):
            _run(world, sched, retention_job)

        assert starrocks.drop.call_count == 1
        assert _live_backup_ids(sched) == set(jobs)
        assert _events(retention_job) == ["RETENTION_STARTED", "ERROR", "FAILED"]
        with session_scope() as session:
            assert {session.get(Job, j).status for j in jobs} == {"SUCCESS"}

    def test_retry_after_a_failure_drops_what_is_left(self, world, starrocks):
        sched = world.schedule(retention=1)
        jobs = [world.job(sched) for _ in range(3)]
        starrocks.drop.side_effect = [None, RuntimeError("boom")]
        with pytest.raises(RuntimeError):
            _run(world, sched, _retention_job(world, sched))
        assert _live_backup_ids(sched) == {jobs[0], jobs[2]}

        starrocks.drop.side_effect = None
        result = _run(world, sched, _retention_job(world, sched))

        assert result["dropped"] == [jobs[0]]
        assert _live_backup_ids(sched) == {jobs[2]}

    @pytest.mark.usefixtures("starrocks")
    def test_deadline_stops_new_drops_and_ends_normally(self, world, monkeypatch):
        sched = world.schedule(retention=1)
        jobs = [world.job(sched) for _ in range(4)]
        retention_job = _retention_job(world, sched)
        clock = iter([0.0, 0.0, 100.0, 100.0, 100.0])
        monkeypatch.setenv("STARROCKS_BR_RETENTION_MAX_SECONDS", "50")
        monkeypatch.setattr(retention_commands.time, "monotonic", lambda: next(clock))

        result = _run(world, sched, retention_job)

        assert result["dropped"] == [jobs[2]]
        assert result["deadline_reached"] is True
        assert _events(retention_job)[-1] == "RETENTION_FINISHED"
        assert _live_backup_ids(sched) == {jobs[0], jobs[1], jobs[3]}

    def test_nothing_to_drop_never_contacts_starrocks(self, world, mocker):
        connect = mocker.patch.object(retention_commands, "connect")
        sched = world.schedule(retention=3)
        world.job(sched)
        retention_job = _retention_job(world, sched)

        result = _run(world, sched, retention_job)

        assert result["dropped"] == []
        connect.assert_not_called()
        assert _events(retention_job) == ["RETENTION_STARTED", "RETENTION_FINISHED"]


class TestSubmitDueRetentionJobs:
    def _open_jobs(self, sched):
        with session_scope() as session:
            return [
                j.id for j in session.query(Job).filter_by(schedule_id=sched, job_type="retention").all()
            ]

    def test_submits_a_retention_job_when_backups_are_droppable(self, world, mocker):
        mocker.patch("starrocks_br.commands.jobs.get_registry").return_value.resolve.return_value = "thread"
        sched = world.schedule(retention=1)
        world.job(sched)
        world.job(sched)

        with session_scope() as session:
            submitted = submit_due_retention_jobs(session)

        assert submitted == self._open_jobs(sched)
        with session_scope() as session:
            job = session.get(Job, submitted[0])
            assert (job.status, job.schedule_id) == ("PENDING", sched)

    def test_no_job_within_the_retention_count(self, world):
        sched = world.schedule(retention=2)
        world.job(sched)
        world.job(sched)

        with session_scope() as session:
            assert submit_due_retention_jobs(session) == []

    def test_no_job_when_only_a_protected_backup_is_beyond_retention(self, world):
        sched = world.schedule(retention=1)
        inc_sched = world.schedule(retention=None, job_type="backup_incremental")
        oldest = world.job(sched)
        world.job(sched)
        world.job(inc_sched, job_type="backup_incremental", baseline=oldest)

        with session_scope() as session:
            assert submit_due_retention_jobs(session) == []

    @pytest.mark.parametrize("status", ["PENDING", "RUNNING"])
    def test_no_duplicate_while_a_retention_job_is_open(self, world, status):
        sched = world.schedule(retention=1)
        world.job(sched)
        world.job(sched)
        _retention_job(world, sched, status=status)

        with session_scope() as session:
            assert submit_due_retention_jobs(session) == []

    def test_a_finished_retention_job_does_not_block_the_next(self, world, mocker):
        mocker.patch("starrocks_br.commands.jobs.get_registry").return_value.resolve.return_value = "thread"
        sched = world.schedule(retention=1)
        world.job(sched)
        world.job(sched)
        _retention_job(world, sched, status="SUCCESS")

        with session_scope() as session:
            assert len(submit_due_retention_jobs(session)) == 1


class TestOrderingWithTheDispatcher:
    @pytest.fixture
    def backend(self, mocker):
        from starrocks_br.commands import jobs as jobs_commands

        backend = MagicMock()
        registry = MagicMock()
        registry.get.return_value = backend
        registry.resolve.return_value = "thread"
        mocker.patch.object(jobs_commands, "get_registry", return_value=registry)
        return backend

    def _status(self, job_id):
        with session_scope() as session:
            return session.get(Job, job_id).status

    def test_retention_waits_pending_while_a_backup_or_restore_is_running(self, world, backend):
        from starrocks_br.commands.jobs import dispatch_pending_jobs

        sched = world.schedule()
        running_backup = world.job(sched, status="RUNNING")
        retention_job = _retention_job(world, sched)

        assert dispatch_pending_jobs().admitted == []
        assert self._status(retention_job) == "PENDING"

        with session_scope() as session:
            session.get(Job, running_backup).status = "SUCCESS"
        world.job(None, status="RUNNING", job_type="restore", refs=False)
        assert dispatch_pending_jobs().admitted == []
        assert self._status(retention_job) == "PENDING"
        backend.enqueue.assert_not_called()

    @pytest.mark.usefixtures("backend")
    def test_a_backup_submitted_during_running_retention_waits_and_does_not_fail(self, world):
        from starrocks_br.commands.jobs import dispatch_pending_jobs

        sched = world.schedule()
        _retention_job(world, sched, status="RUNNING")
        backup = world.job(sched, status="PENDING", refs=False)

        assert dispatch_pending_jobs().admitted == []
        assert self._status(backup) == "PENDING"

    @pytest.mark.usefixtures("backend")
    def test_a_queued_backup_is_admitted_before_a_queued_retention(self, world):
        from starrocks_br.commands.jobs import dispatch_pending_jobs

        sched = world.schedule()
        retention_job = _retention_job(world, sched)
        backup = world.job(sched, status="PENDING", refs=False)

        assert dispatch_pending_jobs().admitted == [backup]
        assert self._status(retention_job) == "PENDING"

    @pytest.mark.usefixtures("starrocks")
    @pytest.mark.usefixtures("backend")
    def test_a_restore_queued_behind_running_retention_protects_its_source(self, world):
        sched = world.schedule(retention=1)
        oldest = world.job(sched)
        world.job(sched)
        retention_job = _retention_job(world, sched, status="RUNNING")
        world.job(None, status="PENDING", job_type="restore", source=oldest, refs=False)

        assert _run(world, sched, retention_job)["dropped"] == []


class TestFailureIsolation:
    def test_a_later_sweep_retries_after_a_failed_retention_job(self, world, mocker, starrocks):
        mocker.patch("starrocks_br.commands.jobs.get_registry").return_value.resolve.return_value = "thread"
        sched = world.schedule(retention=1)
        jobs = [world.job(sched), world.job(sched)]
        failed = _retention_job(world, sched, status="RUNNING")
        starrocks.drop.side_effect = RuntimeError("s3 down")
        with pytest.raises(RuntimeError):
            _run(world, sched, failed)
        with session_scope() as session:
            session.get(Job, failed).status = "FAILED"

        with session_scope() as session:
            resubmitted = submit_due_retention_jobs(session)

        assert len(resubmitted) == 1
        assert jobs[0] in _live_backup_ids(sched)
        with session_scope() as session:
            assert {session.get(Job, j).status for j in jobs} == {"SUCCESS"}
