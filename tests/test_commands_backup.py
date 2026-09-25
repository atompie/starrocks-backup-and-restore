from contextlib import contextmanager

import pytest

from starrocks_br import exceptions
from starrocks_br.commands import backup
from starrocks_br.store.models import Cluster


@pytest.fixture
def cluster():
    return Cluster(
        id=1,
        name="prod-eu",
        host="127.0.0.1",
        port=9030,
        user="root",
        password_encrypted="encrypted-token",
        database="test_db",
        repository="test_repo",
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
        "starrocks_br.labels.determine_backup_label", return_value="test_db_20251016_full"
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
    mocker.patch("starrocks_br.planner.record_backup_partitions")
    execute_backup = mocker.patch(
        "starrocks_br.executor.execute_backup",
        return_value={"success": True, "final_status": {"state": "FINISHED"}},
    )

    result = backup.run_backup_full(cluster, {"group": "production"})

    assert result == {"label": "test_db_20251016_full", "final_status": {"state": "FINISHED"}}
    execute_backup.assert_called_once()
    args, kwargs = execute_backup.call_args
    assert kwargs["backup_type"] == "full"
    assert kwargs["repository"] == "test_repo"
    assert "ops_database" not in kwargs
    assert args[0] is mock_db
    assert args[1] is fake_session
    assert args[2] == cluster.id
    assert args[3] == "BACKUP DATABASE test_db SNAPSHOT test_db_20251016_full TO test_repo"


def test_run_backup_full_raises_on_unhealthy_cluster(
    cluster, mock_decrypt, mock_db, fake_session, mock_unhealthy_cluster
):
    with pytest.raises(RuntimeError, match="health check failed"):
        backup.run_backup_full(cluster, {"group": "production"})


def test_run_backup_full_propagates_snapshot_exists_as_domain_exception(
    cluster, mock_decrypt, mock_db, fake_session, mock_healthy_cluster, mock_repo_exists, mocker
):
    mocker.patch("starrocks_br.labels.determine_backup_label", return_value="lbl")
    mocker.patch("starrocks_br.planner.find_tables_by_group", return_value=[{"database": "d", "table": "t"}])
    mocker.patch("starrocks_br.planner.validate_tables_exist")
    mocker.patch("starrocks_br.planner.build_full_backup_command", return_value="BACKUP ...")
    mocker.patch("starrocks_br.planner.get_all_partitions_for_tables", return_value=[])
    mocker.patch("starrocks_br.concurrency.reserve_job_slot")
    mocker.patch("starrocks_br.planner.record_backup_partitions")
    mocker.patch(
        "starrocks_br.executor.execute_backup",
        return_value={
            "success": False,
            "error_message": "Snapshot 'lbl' already exists in repository",
            "error_details": {"error_type": "snapshot_exists", "snapshot_name": "lbl"},
        },
    )

    with pytest.raises(exceptions.SnapshotAlreadyExistsError) as excinfo:
        backup.run_backup_full(cluster, {"group": "production"})
    assert excinfo.value.snapshot_name == "lbl"


def test_run_backup_full_propagates_other_execute_backup_failure_as_domain_exception(
    cluster, mock_decrypt, mock_db, fake_session, mock_healthy_cluster, mock_repo_exists, mocker
):
    mocker.patch("starrocks_br.labels.determine_backup_label", return_value="lbl")
    mocker.patch("starrocks_br.planner.find_tables_by_group", return_value=[{"database": "d", "table": "t"}])
    mocker.patch("starrocks_br.planner.validate_tables_exist")
    mocker.patch("starrocks_br.planner.build_full_backup_command", return_value="BACKUP ...")
    mocker.patch("starrocks_br.planner.get_all_partitions_for_tables", return_value=[])
    mocker.patch("starrocks_br.concurrency.reserve_job_slot")
    mocker.patch("starrocks_br.planner.record_backup_partitions")
    mocker.patch(
        "starrocks_br.executor.execute_backup",
        return_value={"success": False, "error_message": "boom"},
    )

    with pytest.raises(exceptions.BackupExecutionError, match="boom"):
        backup.run_backup_full(cluster, {"group": "production"})


def test_run_backup_incremental_passes_baseline_and_progress_callback(
    cluster, mock_decrypt, mock_db, fake_session, mock_healthy_cluster, mock_repo_exists, mocker
):
    mocker.patch("starrocks_br.labels.determine_backup_label", return_value="lbl_inc")
    mocker.patch(
        "starrocks_br.planner.find_recent_partitions",
        return_value=[{"database": "d", "table": "t", "partition_name": "p1"}],
    )
    mocker.patch(
        "starrocks_br.planner.build_incremental_backup_command", return_value="BACKUP INC ..."
    )
    mocker.patch("starrocks_br.concurrency.reserve_job_slot")
    mocker.patch("starrocks_br.planner.record_backup_partitions")
    execute_backup = mocker.patch(
        "starrocks_br.executor.execute_backup",
        return_value={"success": True, "final_status": {"state": "FINISHED"}},
    )

    progress_cb = mocker.Mock()
    backup.run_backup_incremental(
        cluster, {"group": "production", "baseline_backup": "base_lbl"}, on_progress=progress_cb
    )

    assert execute_backup.call_args.kwargs["on_progress"] is progress_cb


def test_run_backup_full_raises_clear_error_when_group_missing(cluster, mock_decrypt):
    with pytest.raises(ValueError, match="'group' is required"):
        backup.run_backup_full(cluster, {})


def test_run_backup_incremental_raises_clear_error_when_group_missing(cluster, mock_decrypt):
    with pytest.raises(ValueError, match="'group' is required"):
        backup.run_backup_incremental(cluster, {})
