from contextlib import contextmanager

import pytest

from starrocks_br.commands import prune as prune_command
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

    mocker.patch("starrocks_br.commands.prune.session_scope", _scope)
    return session


def test_run_prune_requires_group(cluster, mock_decrypt):
    with pytest.raises(ValueError, match="'group_id' is required"):
        prune_command.run_prune(cluster, {"keep_last": 1})


def test_run_prune_requires_exactly_one_strategy(cluster, mock_decrypt):
    with pytest.raises(ValueError, match="exactly one"):
        prune_command.run_prune(cluster, {"group_id": 1})


def test_run_prune_deletes_matching_snapshots(
    cluster, mock_decrypt, mock_db, fake_session, mock_healthy_cluster, mock_repo_exists, mocker
):
    get_backups = mocker.patch(
        "starrocks_br.prune.get_successful_backups",
        return_value=[
            {"label": "a", "repository": "test_repo"},
            {"label": "b", "repository": "test_repo"},
        ],
    )
    mocker.patch(
        "starrocks_br.prune.filter_snapshots_to_delete",
        return_value=[{"label": "a", "repository": "test_repo"}],
    )
    drop = mocker.patch("starrocks_br.prune.execute_drop_snapshot")
    cleanup = mocker.patch("starrocks_br.prune.cleanup_backup_history")

    result = prune_command.run_prune(cluster, {"group_id": 1, "keep_last": 1})

    assert result == {"deleted": ["a"], "kept_count": 1}
    get_backups.assert_called_once_with(fake_session, cluster.id, 1)
    drop.assert_called_once_with(mock_db, "test_repo", "a")
    cleanup.assert_called_once_with(fake_session, cluster.id, "a")


def test_run_prune_dry_run_reports_would_delete_without_deleting(
    cluster, mock_decrypt, mock_db, fake_session, mock_healthy_cluster, mock_repo_exists, mocker
):
    """CLI's plan-then-execute adapter (design.md A5) relies on this exact shape."""
    mocker.patch(
        "starrocks_br.prune.get_successful_backups",
        return_value=[
            {"label": "a", "repository": "test_repo"},
            {"label": "b", "repository": "test_repo"},
        ],
    )
    mocker.patch(
        "starrocks_br.prune.filter_snapshots_to_delete",
        return_value=[{"label": "a", "repository": "test_repo"}],
    )
    drop = mocker.patch("starrocks_br.prune.execute_drop_snapshot")
    cleanup = mocker.patch("starrocks_br.prune.cleanup_backup_history")

    result = prune_command.run_prune(cluster, {"group_id": 1, "keep_last": 1, "dry_run": True})

    assert result == {"deleted": [], "would_delete": ["a"], "kept_count": 1}
    drop.assert_not_called()
    cleanup.assert_not_called()


def test_run_prune_no_backups_at_all_omits_would_delete_key(
    cluster, mock_decrypt, mock_db, fake_session, mock_healthy_cluster, mock_repo_exists, mocker
):
    """CLI distinguishes "no backups exist" from "nothing matched" by this key's absence."""
    mocker.patch("starrocks_br.prune.get_successful_backups", return_value=[])

    result = prune_command.run_prune(cluster, {"group_id": 1, "keep_last": 1, "dry_run": True})

    assert result == {"deleted": [], "kept_count": 0}
    assert "would_delete" not in result
