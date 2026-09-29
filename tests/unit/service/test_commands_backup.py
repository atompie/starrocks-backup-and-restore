from contextlib import contextmanager

import pytest

from starrocks_br import exceptions
from starrocks_br.commands import backup
from starrocks_br.store.models import BackupHistory, BackupReference, Cluster, JobStatus, RunStatus


@pytest.fixture
def cluster():
    return Cluster(
        id=1,
        name="prod-eu",
        host="127.0.0.1",
        port=9030,
        user="root",
        password_encrypted="encrypted-token",
        default_backend="thread",
    )


@pytest.fixture
def mock_decrypt(mocker):
    return mocker.patch("starrocks_br.commands._shared.decrypt_password", return_value="plain-pw")


@pytest.fixture
def fake_session(mocker):
    """A stand-in Session object for backup.py's internal `session_scope()` calls.

    Tests in this file mock every ops-table-touching function (labels,
    planner, concurrency, executor) directly, so the actual Session object
    passed through never does real I/O here - only that the same object
    reaches each mocked call is asserted where relevant.
    """
    session = mocker.Mock(name="fake_session")

    @contextmanager
    def _scope():
        yield session

    mocker.patch("starrocks_br.commands.backup.session_scope", _scope)
    return session


def test_run_backup_full_builds_same_command_as_cli(
    cluster, mock_decrypt, mock_db, fake_session, mock_healthy_cluster, mock_repo_exists, mocker
):
    """The command must produce the identical backup command/label the CLI adapter invokes."""
    mocker.patch(
        "starrocks_br.dal.metadata.labels.determine_backup_label", return_value="test_db_20251016_full"
    )
    mocker.patch(
        "starrocks_br.planner.find_tables_by_group",
        return_value=[{"database": "test_db", "table": "orders"}],
    )
    mocker.patch("starrocks_br.planner.validate_tables_exist")
    mocker.patch(
        "starrocks_br.planner.build_full_backup_command",
        return_value="BACKUP DATABASE test_db SNAPSHOT test_db_20251016_full TO test_repo",
    )
    mocker.patch("starrocks_br.planner.get_all_partitions_for_tables", return_value=[])
    mocker.patch("starrocks_br.concurrency.reserve_job_slot")
    mocker.patch("starrocks_br.planner.record_backup_references")
    execute_backup = mocker.patch(
        "starrocks_br.executor.execute_backup",
        return_value={"success": True, "final_status": {"state": "FINISHED"}},
    )

    result = backup.run_backup_full(cluster, {"group_id": 42, "repository": "test_repo"}, job_id=1)

    assert result == {"label": "test_db_20251016_full", "final_status": {"state": "FINISHED"}}
    execute_backup.assert_called_once()
    args, kwargs = execute_backup.call_args
    assert kwargs["backup_type"] == "full"
    assert kwargs["repository"] == "test_repo"
    assert "ops_database" not in kwargs
    assert args[0] is mock_db
    assert args[1] == cluster.id
    assert args[2] == "BACKUP DATABASE test_db SNAPSHOT test_db_20251016_full TO test_repo"


def test_run_backup_full_raises_on_unhealthy_cluster(
    cluster, mock_decrypt, mock_db, fake_session, mock_unhealthy_cluster
):
    with pytest.raises(RuntimeError, match="health check failed"):
        backup.run_backup_full(cluster, {"group_id": 42, "repository": "test_repo"}, job_id=1)


def test_run_backup_full_propagates_snapshot_exists_as_domain_exception(
    cluster, mock_decrypt, mock_db, fake_session, mock_healthy_cluster, mock_repo_exists, mocker
):
    mocker.patch("starrocks_br.dal.metadata.labels.determine_backup_label", return_value="lbl")
    mocker.patch("starrocks_br.planner.find_tables_by_group", return_value=[{"database": "d", "table": "t"}])
    mocker.patch("starrocks_br.planner.validate_tables_exist")
    mocker.patch("starrocks_br.planner.build_full_backup_command", return_value="BACKUP ...")
    mocker.patch("starrocks_br.planner.get_all_partitions_for_tables", return_value=[])
    mocker.patch("starrocks_br.concurrency.reserve_job_slot")
    mocker.patch("starrocks_br.planner.record_backup_references")
    mocker.patch(
        "starrocks_br.executor.execute_backup",
        return_value={
            "success": False,
            "error_message": "Snapshot 'lbl' already exists in repository",
            "error_details": {"error_type": "snapshot_exists", "snapshot_name": "lbl"},
        },
    )

    with pytest.raises(exceptions.SnapshotAlreadyExistsError) as excinfo:
        backup.run_backup_full(cluster, {"group_id": 42, "repository": "test_repo"}, job_id=1)
    assert excinfo.value.snapshot_name == "lbl"


def test_run_backup_full_propagates_other_execute_backup_failure_as_domain_exception(
    cluster, mock_decrypt, mock_db, fake_session, mock_healthy_cluster, mock_repo_exists, mocker
):
    mocker.patch("starrocks_br.dal.metadata.labels.determine_backup_label", return_value="lbl")
    mocker.patch("starrocks_br.planner.find_tables_by_group", return_value=[{"database": "d", "table": "t"}])
    mocker.patch("starrocks_br.planner.validate_tables_exist")
    mocker.patch("starrocks_br.planner.build_full_backup_command", return_value="BACKUP ...")
    mocker.patch("starrocks_br.planner.get_all_partitions_for_tables", return_value=[])
    mocker.patch("starrocks_br.concurrency.reserve_job_slot")
    mocker.patch("starrocks_br.planner.record_backup_references")
    mocker.patch(
        "starrocks_br.executor.execute_backup",
        return_value={"success": False, "error_message": "boom"},
    )

    with pytest.raises(exceptions.BackupExecutionError, match="boom"):
        backup.run_backup_full(cluster, {"group_id": 42, "repository": "test_repo"}, job_id=1)


def test_run_backup_incremental_passes_baseline_and_progress_callback(
    cluster, mock_decrypt, mock_db, fake_session, mock_healthy_cluster, mock_repo_exists, mocker
):
    mocker.patch("starrocks_br.dal.metadata.labels.determine_backup_label", return_value="lbl_inc")
    mocker.patch(
        "starrocks_br.planner.find_tables_by_group",
        return_value=[{"database": "d", "table": "t"}],
    )
    mocker.patch(
        "starrocks_br.planner.find_recent_partitions",
        return_value=([{"database": "d", "table": "t", "partition_name": "p1"}], 7),
    )
    mocker.patch(
        "starrocks_br.planner.build_incremental_backup_command", return_value="BACKUP INC ..."
    )
    mocker.patch("starrocks_br.concurrency.reserve_job_slot")
    mocker.patch("starrocks_br.planner.record_backup_references")
    execute_backup = mocker.patch(
        "starrocks_br.executor.execute_backup",
        return_value={"success": True, "final_status": {"state": "FINISHED"}},
    )

    progress_cb = mocker.Mock()
    backup.run_backup_incremental(
        cluster,
        {"group_id": 42, "repository": "test_repo", "baseline_backup": "base_lbl"},
        job_id=1,
        on_progress=progress_cb,
    )

    assert execute_backup.call_args.kwargs["on_progress"] is progress_cb


def test_run_backup_incremental_records_baseline_job_id(
    cluster, mock_decrypt, mock_db, fake_session, mock_healthy_cluster, mock_repo_exists, mocker
):
    """`Job.baseline_job_id` is set from whatever `find_recent_partitions` resolves as the
    baseline - the full job this incremental backup depends on."""
    mocker.patch("starrocks_br.dal.metadata.labels.determine_backup_label", return_value="lbl_inc")
    mocker.patch(
        "starrocks_br.planner.find_tables_by_group",
        return_value=[{"database": "d", "table": "t"}],
    )
    mocker.patch(
        "starrocks_br.planner.find_recent_partitions",
        return_value=([{"database": "d", "table": "t", "partition_name": "p1"}], 99),
    )
    mocker.patch(
        "starrocks_br.planner.build_incremental_backup_command", return_value="BACKUP INC ..."
    )
    mocker.patch("starrocks_br.concurrency.reserve_job_slot")
    mocker.patch("starrocks_br.planner.record_backup_references")
    mocker.patch(
        "starrocks_br.executor.execute_backup",
        return_value={"success": True, "final_status": {"state": "FINISHED"}},
    )
    set_baseline_job_id = mocker.patch("starrocks_br.dal.metadata.jobs.set_baseline_job_id")

    backup.run_backup_incremental(
        cluster,
        {"group_id": 42, "repository": "test_repo"},
        job_id=1,
    )

    set_baseline_job_id.assert_called_once_with(fake_session, 1, 99)


def test_run_backup_full_raises_clear_error_when_group_missing(cluster, mock_decrypt):
    with pytest.raises(ValueError, match="'group_id' is required"):
        backup.run_backup_full(cluster, {}, job_id=1)


def test_run_backup_incremental_raises_clear_error_when_group_missing(cluster, mock_decrypt):
    with pytest.raises(ValueError, match="'group_id' is required"):
        backup.run_backup_incremental(cluster, {}, job_id=1)


@pytest.fixture
def real_session(sqlite_session, mocker):
    """Route `commands.backup.session_scope` at a real, uncommitted-in-memory `sqlite_session`
    instead of `fake_session`'s Mock, so `planner.record_backup_references` actually persists
    `BackupReference` rows this test can assert against."""

    @contextmanager
    def _scope():
        yield sqlite_session

    mocker.patch("starrocks_br.commands.backup.session_scope", _scope)
    return sqlite_session


def test_run_backup_full_records_no_references_on_failure(
    mock_decrypt, mock_db, real_session, mock_healthy_cluster, mock_repo_exists, mocker, make_cluster, make_job
):
    """A single-database job whose `execute_backup` fails has zero `backup_references` rows
    (SPEC.md §16)."""
    cluster = make_cluster()
    job = make_job(cluster.id, job_type="backup_full", status=JobStatus.PENDING.value)

    mocker.patch("starrocks_br.planner.resolve_group_databases", return_value=["sales_db"])
    mocker.patch("starrocks_br.planner.find_tables_by_group", return_value=[])
    mocker.patch("starrocks_br.planner.validate_tables_exist")
    mocker.patch("starrocks_br.planner.build_full_backup_command", return_value="BACKUP CMD")
    mocker.patch(
        "starrocks_br.dal.metadata.labels.determine_backup_label", return_value="sales_db_lbl"
    )
    mocker.patch(
        "starrocks_br.planner.get_all_partitions_for_tables",
        return_value=[{"database": "sales_db", "table": "t1", "partition_name": "p1"}],
    )
    mocker.patch("starrocks_br.concurrency.reserve_job_slot")
    mocker.patch("starrocks_br.concurrency.complete_job_slot")
    mocker.patch(
        "starrocks_br.executor.execute_backup",
        return_value={"success": False, "error_message": "boom", "final_status": {"state": "FAILED"}},
    )

    with pytest.raises(exceptions.BackupExecutionError, match="boom"):
        backup.run_backup_full(cluster, {"group_id": 42, "repository": "test_repo"}, job_id=job.id)

    assert real_session.query(BackupReference).filter_by(job_id=job.id).count() == 0


def test_run_backup_full_multi_database_writes_references_for_both_databases(
    mock_decrypt, mock_db, real_session, mock_healthy_cluster, mock_repo_exists, mocker, make_cluster, make_job
):
    """A group spanning two databases produces one Job with references for both (PLAN.md §6.5/6.6)."""
    cluster = make_cluster()
    job = make_job(cluster.id, job_type="backup_full", status=JobStatus.PENDING.value)

    mocker.patch(
        "starrocks_br.planner.resolve_group_databases", return_value=["orders_db", "sales_db"]
    )
    mocker.patch("starrocks_br.planner.find_tables_by_group", return_value=[])
    mocker.patch("starrocks_br.planner.validate_tables_exist")
    mocker.patch("starrocks_br.planner.build_full_backup_command", return_value="BACKUP CMD")
    mocker.patch(
        "starrocks_br.dal.metadata.labels.determine_backup_label",
        side_effect=lambda session, cluster_id, backup_type, database, custom_name=None: f"{database}_lbl",
    )
    mocker.patch(
        "starrocks_br.planner.get_all_partitions_for_tables",
        side_effect=lambda db, database, tables: [
            {"database": database, "table": "t1", "partition_name": "p1"}
        ],
    )
    mocker.patch("starrocks_br.concurrency.reserve_job_slot")
    complete_slot = mocker.patch("starrocks_br.concurrency.complete_job_slot")
    mocker.patch(
        "starrocks_br.executor.execute_backup",
        return_value={"success": True, "final_status": {"state": "FINISHED"}},
    )

    result = backup.run_backup_full(cluster, {"group_id": 42, "repository": "test_repo"}, job_id=job.id)

    assert result["label"] == "orders_db_lbl"
    rows = real_session.query(BackupReference).filter_by(job_id=job.id).all()
    assert {(r.database_name, r.snapshot_label) for r in rows} == {
        ("orders_db", "orders_db_lbl"),
        ("sales_db", "sales_db_lbl"),
    }
    # The concurrency slot is released exactly once for the whole job, not once per database.
    complete_slot.assert_called_once()
    assert complete_slot.call_args.kwargs["final_state"] == "FINISHED"


def test_run_backup_full_multi_database_stops_and_keeps_only_prior_successes_on_failure(
    mock_decrypt, mock_db, real_session, mock_healthy_cluster, mock_repo_exists, mocker, make_cluster, make_job
):
    """The second database's failure stops the loop; the first database's already-recorded
    reference is not retroactively removed (see design.md Risks - a `FAILED` job's stray
    reference rows are inert since restore already rejects a non-`SUCCESS` job)."""
    cluster = make_cluster()
    job = make_job(cluster.id, job_type="backup_full", status=JobStatus.PENDING.value)

    mocker.patch(
        "starrocks_br.planner.resolve_group_databases", return_value=["orders_db", "sales_db"]
    )
    mocker.patch("starrocks_br.planner.find_tables_by_group", return_value=[])
    mocker.patch("starrocks_br.planner.validate_tables_exist")
    mocker.patch("starrocks_br.planner.build_full_backup_command", return_value="BACKUP CMD")
    mocker.patch(
        "starrocks_br.dal.metadata.labels.determine_backup_label",
        side_effect=lambda session, cluster_id, backup_type, database, custom_name=None: f"{database}_lbl",
    )
    mocker.patch(
        "starrocks_br.planner.get_all_partitions_for_tables",
        side_effect=lambda db, database, tables: [
            {"database": database, "table": "t1", "partition_name": "p1"}
        ],
    )
    mocker.patch("starrocks_br.concurrency.reserve_job_slot")
    complete_slot = mocker.patch("starrocks_br.concurrency.complete_job_slot")
    mocker.patch(
        "starrocks_br.executor.execute_backup",
        side_effect=[
            {"success": True, "final_status": {"state": "FINISHED"}},
            {"success": False, "error_message": "boom", "final_status": {"state": "FAILED"}},
        ],
    )

    with pytest.raises(exceptions.BackupExecutionError, match="boom"):
        backup.run_backup_full(cluster, {"group_id": 42, "repository": "test_repo"}, job_id=job.id)

    rows = real_session.query(BackupReference).filter_by(job_id=job.id).all()
    assert {r.database_name for r in rows} == {"orders_db"}
    complete_slot.assert_called_once()
    assert complete_slot.call_args.kwargs["final_state"] == "FAILED"


def test_run_backup_full_execute_backup_runs_with_no_open_session(
    cluster, mock_decrypt, mock_db, mock_healthy_cluster, mock_repo_exists, mocker
):
    """No metadata-store session may be open while `execute_backup` runs - it polls StarRocks for
    up to a day, and AGENTS.md forbids holding a transaction open across a StarRocks operation."""
    open_scopes = []

    @contextmanager
    def _tracking_scope():
        open_scopes.append(1)
        try:
            yield mocker.Mock()
        finally:
            open_scopes.pop()

    mocker.patch("starrocks_br.commands.backup.session_scope", _tracking_scope)
    mocker.patch("starrocks_br.dal.metadata.labels.determine_backup_label", return_value="lbl")
    mocker.patch(
        "starrocks_br.planner.find_tables_by_group", return_value=[{"database": "d", "table": "t"}]
    )
    mocker.patch("starrocks_br.planner.validate_tables_exist")
    mocker.patch("starrocks_br.planner.build_full_backup_command", return_value="BACKUP ...")
    mocker.patch("starrocks_br.planner.get_all_partitions_for_tables", return_value=[])
    mocker.patch("starrocks_br.concurrency.reserve_job_slot")
    mocker.patch("starrocks_br.concurrency.complete_job_slot")
    mocker.patch("starrocks_br.planner.record_backup_references")

    open_scope_count_during_execute_backup = []

    def _execute_backup(*args, **kwargs):
        open_scope_count_during_execute_backup.append(len(open_scopes))
        return {"success": True, "final_status": {"state": "FINISHED"}}

    mocker.patch("starrocks_br.executor.execute_backup", side_effect=_execute_backup)

    backup.run_backup_full(cluster, {"group_id": 42, "repository": "test_repo"}, job_id=1)

    assert open_scope_count_during_execute_backup == [0]


def test_run_backup_full_releases_slot_when_failure_precedes_execute_backup(
    mock_decrypt,
    mock_db,
    real_session,
    mock_healthy_cluster,
    mock_repo_exists,
    mock_validate_tables_exist,
    mocker,
    make_cluster,
    make_job,
    history_session_factory,
):
    """A failure raised before `execute_backup` is ever called (e.g. no tables to back up) must
    still release the `backup` concurrency slot - not just failures `execute_backup` itself
    reports - and must record why the job failed."""
    cluster = make_cluster()
    job = make_job(cluster.id, job_type="backup_full", status=JobStatus.PENDING.value)
    mocker.patch(
        "starrocks_br.commands.backup.get_session_factory", return_value=history_session_factory
    )

    mocker.patch("starrocks_br.planner.resolve_group_databases", return_value=["sales_db"])
    mocker.patch("starrocks_br.planner.find_tables_by_group", return_value=[])
    mocker.patch(
        "starrocks_br.dal.metadata.labels.determine_backup_label", return_value="sales_db_lbl"
    )
    mocker.patch("starrocks_br.planner.build_full_backup_command", return_value=None)
    execute_backup = mocker.patch("starrocks_br.executor.execute_backup")

    with pytest.raises(RuntimeError, match="No tables found"):
        backup.run_backup_full(cluster, {"group_id": 42, "repository": "test_repo"}, job_id=job.id)

    execute_backup.assert_not_called()

    row = real_session.query(RunStatus).filter_by(cluster_id=cluster.id, scope="backup").one()
    assert row.state == "FAILED"

    history_rows = real_session.query(BackupHistory).filter_by(job_id=job.id).all()
    assert len(history_rows) == 1
    assert history_rows[0].status == "FAILED"
    assert "No tables found" in history_rows[0].message


def test_run_backup_full_releases_slot_when_recording_references_fails_after_success(
    mock_decrypt,
    mock_db,
    real_session,
    mock_healthy_cluster,
    mock_repo_exists,
    mock_validate_tables_exist,
    mocker,
    make_cluster,
    make_job,
    history_session_factory,
):
    """A failure recording backup references after a successful `execute_backup` call must still
    release the slot and leave a `FAILED` history entry explaining why."""
    cluster = make_cluster()
    job = make_job(cluster.id, job_type="backup_full", status=JobStatus.PENDING.value)
    mocker.patch(
        "starrocks_br.commands.backup.get_session_factory", return_value=history_session_factory
    )

    mocker.patch("starrocks_br.planner.resolve_group_databases", return_value=["sales_db"])
    mocker.patch(
        "starrocks_br.planner.find_tables_by_group", return_value=[{"database": "sales_db", "table": "t"}]
    )
    mocker.patch(
        "starrocks_br.dal.metadata.labels.determine_backup_label", return_value="sales_db_lbl"
    )
    mocker.patch("starrocks_br.planner.build_full_backup_command", return_value="BACKUP CMD")
    mocker.patch("starrocks_br.planner.get_all_partitions_for_tables", return_value=[])
    mocker.patch(
        "starrocks_br.executor.execute_backup",
        return_value={"success": True, "final_status": {"state": "FINISHED"}},
    )
    mocker.patch(
        "starrocks_br.planner.record_backup_references", side_effect=RuntimeError("db write failed")
    )

    with pytest.raises(RuntimeError, match="db write failed"):
        backup.run_backup_full(cluster, {"group_id": 42, "repository": "test_repo"}, job_id=job.id)

    row = real_session.query(RunStatus).filter_by(cluster_id=cluster.id, scope="backup").one()
    assert row.state == "FAILED"

    history_rows = real_session.query(BackupHistory).filter_by(job_id=job.id).all()
    assert len(history_rows) == 1
    assert history_rows[0].status == "FAILED"
    assert "db write failed" in history_rows[0].message


def test_run_backup_incremental_releases_slot_when_failure_precedes_execute_backup(
    mock_decrypt,
    mock_db,
    real_session,
    mock_healthy_cluster,
    mock_repo_exists,
    mocker,
    make_cluster,
    make_job,
    history_session_factory,
):
    """Same guarantee as the full-backup case, for the incremental loop."""
    cluster = make_cluster()
    job = make_job(cluster.id, job_type="backup_incremental", status=JobStatus.PENDING.value)
    mocker.patch(
        "starrocks_br.commands.backup.get_session_factory", return_value=history_session_factory
    )

    mocker.patch("starrocks_br.planner.resolve_group_databases", return_value=["sales_db"])
    mocker.patch(
        "starrocks_br.dal.metadata.labels.determine_backup_label", return_value="sales_db_lbl"
    )
    mocker.patch("starrocks_br.planner.find_recent_partitions", return_value=([], 99))
    execute_backup = mocker.patch("starrocks_br.executor.execute_backup")

    with pytest.raises(RuntimeError, match="No partitions found"):
        backup.run_backup_incremental(
            cluster, {"group_id": 42, "repository": "test_repo"}, job_id=job.id
        )

    execute_backup.assert_not_called()

    row = real_session.query(RunStatus).filter_by(cluster_id=cluster.id, scope="backup").one()
    assert row.state == "FAILED"

    history_rows = real_session.query(BackupHistory).filter_by(job_id=job.id).all()
    assert len(history_rows) == 1
    assert history_rows[0].status == "FAILED"
    assert "No partitions found" in history_rows[0].message

