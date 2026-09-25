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

"""Parity check: the same inputs must produce the same backup command/label
whether driven through the existing CLI `backup full` command or through
the new API job submission path (jobs/handlers.py), per
specs/api-job-execution "Job execution reuses existing backup/restore/prune
behavior unchanged".
"""

from click.testing import CliRunner

from starrocks_br import cli
from starrocks_br.jobs import handlers
from starrocks_br.store.models import Cluster

# Matches tests/conftest.py's config_file fixture (host/port/user/database/repository).
SHARED_CONNECTION = {
    "host": "127.0.0.1",
    "port": 9030,
    "user": "root",
    "database": "test_db",
    "repository": "test_repo",
}


def test_cli_and_api_produce_the_same_backup_command_and_label(
    config_file,
    mock_db,
    mock_initialized_schema,
    mock_healthy_cluster,
    mock_repo_exists,
    mock_validate_tables_exist,
    setup_password_env,
    mocker,
):
    mocker.patch(
        "starrocks_br.planner.find_tables_by_group",
        return_value=[{"database": "test_db", "table": "dim_customers"}],
    )
    mocker.patch("starrocks_br.planner.get_all_partitions_for_tables", return_value=[])
    mocker.patch("starrocks_br.concurrency.reserve_job_slot")
    mocker.patch("starrocks_br.planner.record_backup_partitions")
    mocker.patch("starrocks_br.labels.determine_backup_label", return_value="test_db_20251016_full")

    execute_backup = mocker.patch(
        "starrocks_br.executor.execute_backup",
        return_value={"success": True, "final_status": {"state": "FINISHED"}, "error_message": None},
    )

    # --- CLI path ---
    runner = CliRunner()
    cli_result = runner.invoke(cli.backup_full, ["--config", config_file, "--group", "weekly_dimensions"])
    assert cli_result.exit_code == 0
    cli_command = execute_backup.call_args[0][1]

    execute_backup.reset_mock()

    # --- API job-handler path, same inputs ---
    mocker.patch("starrocks_br.jobs.handlers.decrypt_password", return_value="test_password")
    cluster = Cluster(
        id=1,
        name="prod-eu",
        password_encrypted="enc",
        ops_database="ops",
        default_backend="thread",
        **SHARED_CONNECTION,
    )
    handlers.run_backup_full(cluster, {"group": "weekly_dimensions"})
    api_command = execute_backup.call_args[0][1]

    assert api_command == cli_command
