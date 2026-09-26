import pytest

from starrocks_br import exceptions, repository
from starrocks_br.commands import repositories as repository_commands


@pytest.fixture
def mock_database(mocker):
    return mocker.Mock(name="database")


def test_create_repository_raises_already_exists_on_conflict(mock_database, mocker):
    mocker.patch(
        "starrocks_br.repository.build_create_s3_repository_command", return_value="CREATE ..."
    )
    mock_database.execute.side_effect = Exception("Repository already exist")

    with pytest.raises(exceptions.RepositoryAlreadyExistsError) as excinfo:
        repository_commands.create_repository(
            mock_database, "my-cluster", "my-repo", "s3://loc", "ak", "sk", None, None
        )
    assert "my-repo" in str(excinfo.value)
    assert "my-cluster" in str(excinfo.value)


def test_create_repository_reraises_other_errors(mock_database, mocker):
    mocker.patch(
        "starrocks_br.repository.build_create_s3_repository_command", return_value="CREATE ..."
    )
    mock_database.execute.side_effect = RuntimeError("connection refused")

    with pytest.raises(RuntimeError, match="connection refused"):
        repository_commands.create_repository(
            mock_database, "my-cluster", "my-repo", "s3://loc", "ak", "sk", None, None
        )


def test_create_repository_returns_created_repo_from_listing(mock_database, mocker):
    mocker.patch(
        "starrocks_br.repository.build_create_s3_repository_command", return_value="CREATE ..."
    )
    mocker.patch(
        "starrocks_br.repository.list_repositories",
        return_value=[{"name": "my-repo", "location": "s3://loc", "broker": None, "is_read_only": False}],
    )

    result = repository_commands.create_repository(
        mock_database, "my-cluster", "my-repo", "s3://loc", "ak", "sk", None, None
    )

    assert result["name"] == "my-repo"


def test_create_repository_falls_back_when_not_listed_back(mock_database, mocker):
    mocker.patch(
        "starrocks_br.repository.build_create_s3_repository_command", return_value="CREATE ..."
    )
    mocker.patch("starrocks_br.repository.list_repositories", return_value=[])

    result = repository_commands.create_repository(
        mock_database, "my-cluster", "my-repo", "s3://loc", "ak", "sk", None, None
    )

    assert result == {
        "name": "my-repo",
        "location": "s3://loc",
        "broker": None,
        "is_read_only": False,
        "error": None,
    }


def test_delete_repository_raises_still_has_snapshots(mock_database, mocker):
    mocker.patch("starrocks_br.repository.has_snapshots", return_value=True)
    drop = mocker.patch("starrocks_br.repository.drop_repository")

    with pytest.raises(exceptions.RepositoryStillHasSnapshotsError, match="my-repo"):
        repository_commands.delete_repository(mock_database, "my-repo")
    drop.assert_not_called()


def test_delete_repository_propagates_not_found(mock_database, mocker):
    mocker.patch(
        "starrocks_br.repository.has_snapshots",
        side_effect=repository.RepositoryNotFoundError("not found"),
    )

    with pytest.raises(repository.RepositoryNotFoundError):
        repository_commands.delete_repository(mock_database, "my-repo")


def test_delete_repository_drops_when_no_snapshots(mock_database, mocker):
    mocker.patch("starrocks_br.repository.has_snapshots", return_value=False)
    drop = mocker.patch("starrocks_br.repository.drop_repository")

    repository_commands.delete_repository(mock_database, "my-repo")

    drop.assert_called_once_with(mock_database, "my-repo")
