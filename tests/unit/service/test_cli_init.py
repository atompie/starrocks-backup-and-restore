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

from click.testing import CliRunner

from starrocks_br import cli


def test_init_command_success(config_file, mock_db, mock_resolved_cluster, setup_password_env, mocker):
    """Test successful init command."""
    runner = CliRunner()

    mocker.patch("starrocks_br.repository.ensure_repository")

    result = runner.invoke(cli.init, ["--config", config_file])

    assert result.exit_code == 0
    assert "Cluster registered" in result.output
    assert "Next steps:" in result.output
    assert "Populate your table inventory" in result.output


def test_init_bootstraps_table_inventory_when_configured(
    mock_db, mock_resolved_cluster, setup_password_env, mocker, tmp_path
):
    """Init command should bootstrap table_inventory rows from the config's YAML section."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
        host: "127.0.0.1"
        port: 9030
        user: "root"
        database: "test_db"
        repository: "test_repo"
        table_inventory:
          - group: "daily_incremental"
            tables:
              - database: "test_db"
                table: "fact_table"
        """
    )
    mocker.patch("starrocks_br.repository.ensure_repository")
    bootstrap = mocker.patch("starrocks_br.inventory_groups.bootstrap_table_inventory")

    runner = CliRunner()
    result = runner.invoke(cli.init, ["--config", str(config_path)])

    assert result.exit_code == 0
    bootstrap.assert_called_once()
    args = bootstrap.call_args[0]
    assert args[1] == mock_resolved_cluster.id
    assert args[2] == [("daily_incremental", "test_db", "fact_table")]
    assert "Table inventory bootstrapped from config with 1 entries" in result.output


def test_init_validates_repository_exists(config_file, mock_db, mock_resolved_cluster, setup_password_env, mocker):
    """Init command should validate that repository exists before registering the cluster."""
    runner = CliRunner()

    mock_ensure_repo = mocker.patch("starrocks_br.repository.ensure_repository")

    result = runner.invoke(cli.init, ["--config", config_file])

    assert result.exit_code == 0
    mock_ensure_repo.assert_called_once_with(mock_db, "test_repo")


def test_init_fails_when_repository_not_found(config_file, mock_db, setup_password_env, mocker):
    """Init command should fail with clear error when repository doesn't exist."""
    runner = CliRunner()

    mocker.patch(
        "starrocks_br.repository.ensure_repository",
        side_effect=RuntimeError(
            "Repository 'test_repo' not found. Please create it first using:\n"
            "  CREATE REPOSITORY test_repo WITH BROKER ON LOCATION '...' PROPERTIES(...)\n"
            "For examples, see: https://docs.starrocks.io/docs/sql-reference/sql-statements/data-definition/backup_restore/CREATE_REPOSITORY/"
        ),
    )

    result = runner.invoke(cli.init, ["--config", config_file])

    assert result.exit_code == 1
    assert "Repository 'test_repo' not found" in result.output
    assert "CREATE REPOSITORY" in result.output


def test_init_fails_when_repository_has_errors(config_file, mock_db, setup_password_env, mocker):
    """Init command should fail when repository exists but has errors."""
    runner = CliRunner()

    mocker.patch(
        "starrocks_br.repository.ensure_repository",
        side_effect=RuntimeError(
            "Repository 'test_repo' has errors: Connection failed: auth error"
        ),
    )

    result = runner.invoke(cli.init, ["--config", config_file])

    assert result.exit_code == 1
    assert "Repository 'test_repo' has errors" in result.output
    assert "Connection failed" in result.output
