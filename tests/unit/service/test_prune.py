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

"""Unit tests for the prune module."""


import pytest

from starrocks_br import prune


class TestVerifySnapshotExists:
    """Unit tests for verify_snapshot_exists function."""

    def test_snapshot_exists(self, mocker):
        """Test when snapshot exists in repository."""
        mock_db = mocker.Mock()
        mock_db.query.return_value = [["snapshot_data"]]

        result = prune.verify_snapshot_exists(mock_db, "test_repo", "backup1")

        assert result is True
        query_sql = mock_db.query.call_args[0][0]
        assert "SHOW SNAPSHOT ON `test_repo`" in query_sql
        assert "SNAPSHOT = 'backup1'" in query_sql

    def test_snapshot_name_with_quote_is_escaped(self, mocker):
        """A snapshot name containing a single quote must not break out of the SQL string literal."""
        mock_db = mocker.Mock()
        mock_db.query.return_value = [["snapshot_data"]]

        prune.verify_snapshot_exists(mock_db, "test_repo", "o'brien_backup")

        query_sql = mock_db.query.call_args[0][0]
        assert "SNAPSHOT = 'o''brien_backup'" in query_sql

    def test_snapshot_not_found(self, mocker):
        """Test when snapshot doesn't exist in repository."""
        mock_db = mocker.Mock()
        mock_db.query.return_value = []

        with pytest.raises(Exception, match="not found"):
            prune.verify_snapshot_exists(mock_db, "test_repo", "nonexistent")

    def test_snapshot_query_error(self, mocker):
        """Test when query fails."""
        mock_db = mocker.Mock()
        mock_db.query.side_effect = Exception("Database error")

        with pytest.raises(Exception, match="Database error"):
            prune.verify_snapshot_exists(mock_db, "test_repo", "backup1")


class TestExecuteDropSnapshot:
    """Unit tests for execute_drop_snapshot function."""

    def test_drop_snapshot_success(self, mocker):
        """Test successful snapshot deletion."""
        mock_db = mocker.Mock()

        prune.execute_drop_snapshot(mock_db, "test_repo", "backup1")

        mock_db.execute.assert_called_once()
        sql = mock_db.execute.call_args[0][0]
        assert "DROP SNAPSHOT ON `test_repo`" in sql
        assert "SNAPSHOT = 'backup1'" in sql

    def test_drop_snapshot_failure(self, mocker):
        """Test snapshot deletion failure."""
        mock_db = mocker.Mock()
        mock_db.execute.side_effect = Exception("Drop failed")

        with pytest.raises(Exception, match="Drop failed"):
            prune.execute_drop_snapshot(mock_db, "test_repo", "backup1")
