# Copyright 2025 deep-bi
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import pytest

from starrocks_br.exceptions import RepositoryUnreachableError
from starrocks_br.repository import (
    RepositoryNotFoundError,
    build_create_s3_repository_command,
    drop_repository,
    ensure_repository,
    has_snapshots,
    list_repositories,
)


def test_should_raise_when_repository_not_found(mocker):
    """Test that ensure_repository raises an error when repository doesn't exist."""
    db = mocker.Mock()
    db.query.return_value = []

    with pytest.raises(RuntimeError) as err:
        ensure_repository(db, "missing_repo")

    assert "not found" in str(err.value).lower()
    assert "missing_repo" in str(err.value)
    assert db.query.call_count >= 1


def test_should_pass_when_repository_exists(mocker):
    """Test that ensure_repository succeeds when repository exists without errors."""
    db = mocker.Mock()
    db.query.return_value = [
        # | RepoId | RepoName | CreateTime | IsReadOnly | Location | Broker | ErrMsg |
        (
            "34217",
            "minio_repo",
            "2025-10-16 19:00:05",
            "false",
            "s3://backups/starrocks/",
            "",
            "NULL",
        )
    ]

    # Should not raise
    ensure_repository(db, "minio_repo")

    assert db.query.call_count >= 1


def test_should_raise_when_repository_has_errors(mocker):
    """Test that ensure_repository raises an error when repository has error message."""
    db = mocker.Mock()
    db.query.return_value = [
        # | RepoId | RepoName | CreateTime | IsReadOnly | Location | Broker | ErrMsg |
        (
            "34217",
            "broken_repo",
            "2025-10-16 19:00:05",
            "false",
            "s3://backups/",
            "",
            "Connection failed: auth error",
        )
    ]

    with pytest.raises(RuntimeError) as err:
        ensure_repository(db, "broken_repo")

    assert "auth error" in str(err.value).lower()
    assert "broken_repo" in str(err.value)


def test_list_repositories_parses_tuple_rows(mocker):
    db = mocker.Mock()
    db.query.return_value = [
        ("34217", "minio_repo", "2025-10-16 19:00:05", "false", "s3://backups/starrocks/", "", "NULL"),
        ("34218", "broken_repo", "2025-10-16 19:00:05", "true", "s3://backups/", "broker1", "auth error"),
    ]

    result = list_repositories(db)

    assert result == [
        {
            "name": "minio_repo",
            "location": "s3://backups/starrocks/",
            "broker": "",
            "is_read_only": False,
            "error": None,
        },
        {
            "name": "broken_repo",
            "location": "s3://backups/",
            "broker": "broker1",
            "is_read_only": True,
            "error": "auth error",
        },
    ]


def test_list_repositories_parses_dict_rows(mocker):
    db = mocker.Mock()
    db.query.return_value = [
        {
            "RepoId": "1",
            "RepoName": "minio_repo",
            "CreateTime": "2025-10-16 19:00:05",
            "IsReadOnly": "false",
            "Location": "s3://backups/starrocks/",
            "Broker": "",
            "ErrMsg": "",
        }
    ]

    result = list_repositories(db)

    assert result == [
        {
            "name": "minio_repo",
            "location": "s3://backups/starrocks/",
            "broker": "",
            "is_read_only": False,
            "error": None,
        }
    ]


def test_build_create_s3_repository_command_generates_expected_sql():
    command = build_create_s3_repository_command(
        name="my_repo",
        location="s3://bucket/path",
        access_key="AK",
        secret_key="SK",
        endpoint="https://s3.amazonaws.com",
        region="us-west-2",
    )

    assert command == (
        "CREATE REPOSITORY `my_repo`\n"
        "WITH BROKER\n"
        "ON LOCATION 's3://bucket/path'\n"
        "PROPERTIES (\n"
        "    'aws.s3.access_key' = 'AK',\n"
        "    'aws.s3.secret_key' = 'SK',\n"
        "    'aws.s3.endpoint' = 'https://s3.amazonaws.com',\n"
        "    'aws.s3.enable_path_style_access' = 'true',\n"
        "    'aws.s3.enable_ssl' = 'true',\n"
        "    'aws.s3.region' = 'us-west-2'\n"
        ")"
    )


def test_build_create_s3_repository_command_strips_trailing_slash_to_avoid_double_slash():
    command = build_create_s3_repository_command(
        name="my_repo",
        location="s3://bucket/path/",
        access_key="AK",
        secret_key="SK",
        endpoint="https://s3.amazonaws.com",
    )

    assert "ON LOCATION 's3://bucket/path'\n" in command
    assert "path//" not in command


def test_build_create_s3_repository_command_omits_region_when_not_provided():
    command = build_create_s3_repository_command(
        name="my_repo",
        location="s3://bucket/path",
        access_key="AK",
        secret_key="SK",
        endpoint="https://s3.amazonaws.com",
    )

    assert "aws.s3.region" not in command


def test_build_create_s3_repository_command_derives_ssl_flag_from_endpoint_scheme():
    https_command = build_create_s3_repository_command(
        name="my_repo",
        location="s3://bucket/path",
        access_key="AK",
        secret_key="SK",
        endpoint="https://s3.amazonaws.com",
    )
    http_command = build_create_s3_repository_command(
        name="my_repo",
        location="s3://bucket/path",
        access_key="AK",
        secret_key="SK",
        endpoint="http://localhost:9000",
    )

    assert "'aws.s3.enable_ssl' = 'true'" in https_command
    assert "'aws.s3.enable_ssl' = 'false'" in http_command
    assert "'aws.s3.enable_path_style_access' = 'true'" in https_command
    assert "'aws.s3.enable_path_style_access' = 'true'" in http_command


def _mock_db_with_repository(mocker, repo_name, snapshot_rows=None, snapshot_error=None):
    """A `db.query` mock that answers `SHOW REPOSITORIES` with `repo_name`
    present, and any other query (i.e. `SHOW SNAPSHOT ON ...`) with either
    `snapshot_rows` or by raising `snapshot_error`."""

    def query(sql):
        if sql == "SHOW REPOSITORIES":
            return [(1, repo_name, "2025-01-01", "false", "s3://bucket/path", "", "NULL")]
        if snapshot_error is not None:
            raise snapshot_error
        return snapshot_rows

    db = mocker.Mock()
    db.query.side_effect = query
    return db


def test_has_snapshots_returns_false_when_empty(mocker):
    db = _mock_db_with_repository(mocker, "my_repo", snapshot_rows=[])

    assert has_snapshots(db, "my_repo") is False


def test_has_snapshots_returns_true_when_non_empty(mocker):
    db = _mock_db_with_repository(mocker, "my_repo", snapshot_rows=[("snap1", "2025-10-16", "OK")])

    assert has_snapshots(db, "my_repo") is True


def test_has_snapshots_raises_not_found_when_repository_is_not_registered(mocker):
    db = mocker.Mock()
    db.query.return_value = []  # SHOW REPOSITORIES lists nothing at all

    with pytest.raises(RepositoryNotFoundError):
        has_snapshots(db, "missing_repo")


def test_has_snapshots_raises_unreachable_when_registered_but_snapshot_check_fails(mocker):
    # Regression test: a repository whose storage backend is unreachable
    # produces a StarRocks error that also mentions "repository" and
    # "exist" (e.g. "failed to check remote path exist: ... Repository
    # [my_repo] ..."), which used to be misclassified as RepositoryNotFoundError
    # even though `SHOW REPOSITORIES` confirms the repository is registered.
    db = _mock_db_with_repository(
        mocker,
        "my_repo",
        snapshot_error=RuntimeError(
            "failed to check remote path exist: s3://bucket/__starrocks_repository_my_repo"
        ),
    )

    with pytest.raises(RepositoryUnreachableError) as err:
        has_snapshots(db, "my_repo")

    assert "my_repo" in str(err.value)


def test_drop_repository_executes_exact_sql(mocker):
    db = mocker.Mock()

    drop_repository(db, "my_repo")

    db.execute.assert_called_once_with("DROP REPOSITORY `my_repo`")
