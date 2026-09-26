from contextlib import contextmanager

import pytest

from starrocks_br import exceptions
from starrocks_br.commands import restore as restore_command
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
        default_backend="thread",
    )


@pytest.fixture
def mock_decrypt(mocker):
    return mocker.patch("starrocks_br.commands._shared.decrypt_password", return_value="plain-pw")


@pytest.fixture
def fake_session(mocker):
    session = mocker.Mock(name="fake_session")

    @contextmanager
    def _scope():
        yield session

    mocker.patch("starrocks_br.commands.restore.session_scope", _scope)
    return session


def test_run_restore_rejects_group_and_table_together(cluster, mock_decrypt):
    with pytest.raises(ValueError, match="Cannot specify both"):
        restore_command.run_restore(cluster, {"target_label": "lbl", "group_id": 42, "table": "t"})


def test_run_restore_delegates_to_execute_restore_flow(
    cluster, mock_decrypt, mock_db, fake_session, mock_healthy_cluster, mock_repo_exists, mocker
):
    mocker.patch("starrocks_br.restore.find_restore_pair", return_value=["full_lbl"])
    mocker.patch("starrocks_br.restore.get_tables_from_backup", return_value=["d.t"])
    execute_flow = mocker.patch(
        "starrocks_br.restore.execute_restore_flow",
        return_value={"success": True, "message": "done"},
    )

    result = restore_command.run_restore(cluster, {"target_label": "full_lbl"})

    assert result["restore_pair"] == ["full_lbl"]
    assert result["tables"] == ["d.t"]
    execute_flow.assert_called_once()
    args, kwargs = execute_flow.call_args
    assert args[0] is mock_db
    assert args[1] is fake_session
    assert args[2] == cluster.id
    assert kwargs["skip_confirmation"] is True


def test_run_restore_honors_skip_confirmation_false(
    cluster, mock_decrypt, mock_db, fake_session, mock_healthy_cluster, mock_repo_exists, mocker
):
    """CLI's `--yes` flag must reach `restore.execute_restore_flow`'s own confirmation prompt."""
    mocker.patch("starrocks_br.restore.find_restore_pair", return_value=["full_lbl"])
    mocker.patch("starrocks_br.restore.get_tables_from_backup", return_value=["d.t"])
    execute_flow = mocker.patch(
        "starrocks_br.restore.execute_restore_flow",
        return_value={"success": True, "message": "done"},
    )

    restore_command.run_restore(cluster, {"target_label": "full_lbl"}, skip_confirmation=False)

    assert execute_flow.call_args.kwargs["skip_confirmation"] is False


def test_run_restore_raises_no_tables_found_error_when_backup_has_no_matching_tables(
    cluster, mock_decrypt, mock_db, fake_session, mock_healthy_cluster, mock_repo_exists, mocker
):
    mocker.patch("starrocks_br.restore.find_restore_pair", return_value=["full_lbl"])
    mocker.patch("starrocks_br.restore.get_tables_from_backup", return_value=[])

    with pytest.raises(exceptions.NoTablesFoundError):
        restore_command.run_restore(cluster, {"target_label": "full_lbl", "group_id": 42})


def test_run_restore_raises_restore_execution_error_on_flow_failure(
    cluster, mock_decrypt, mock_db, fake_session, mock_healthy_cluster, mock_repo_exists, mocker
):
    mocker.patch("starrocks_br.restore.find_restore_pair", return_value=["full_lbl"])
    mocker.patch("starrocks_br.restore.get_tables_from_backup", return_value=["d.t"])
    mocker.patch(
        "starrocks_br.restore.execute_restore_flow",
        return_value={"success": False, "error_message": "permission denied"},
    )

    with pytest.raises(exceptions.RestoreExecutionError, match="permission denied"):
        restore_command.run_restore(cluster, {"target_label": "full_lbl"})
