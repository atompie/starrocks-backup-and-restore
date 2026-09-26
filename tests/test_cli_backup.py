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

from starrocks_br import cli, inventory_groups


def test_backup_incremental_success(
    config_file,
    mock_db,
    mock_resolved_cluster,
    mock_healthy_cluster,
    mock_repo_exists,
    setup_password_env,
    mocker,
):
    """Test successful incremental backup with default baseline (latest full backup)."""
    runner = CliRunner()

    mocker.patch(
        "starrocks_br.planner.find_latest_full_backup",
        return_value={
            "label": "test_db_20251015_full",
            "backup_type": "full",
            "finished_at": "2025-10-15 10:00:00",
        },
    )
    mocker.patch(
        "starrocks_br.planner.find_tables_by_group",
        return_value=[{"database": "test_db", "table": "fact_table"}],
    )
    mocker.patch(
        "starrocks_br.planner.find_recent_partitions",
        return_value=[
            {"database": "test_db", "table": "fact_table", "partition_name": "p20251016"}
        ],
    )
    mocker.patch("starrocks_br.labels.determine_backup_label", return_value="test_db_20251016_inc")
    mocker.patch(
        "starrocks_br.planner.build_incremental_backup_command",
        return_value="BACKUP DATABASE test_db SNAPSHOT test_db_20251016_inc TO test_repo",
    )
    mocker.patch("starrocks_br.concurrency.reserve_job_slot")
    mocker.patch("starrocks_br.planner.record_backup_partitions")
    mocker.patch(
        "starrocks_br.executor.execute_backup",
        return_value={
            "success": True,
            "final_status": {"state": "FINISHED"},
            "error_message": None,
        },
    )

    result = runner.invoke(
        cli.backup_incremental, ["--config", config_file, "--group", "daily_incremental"]
    )

    assert result.exit_code == 0
    assert "Backup completed successfully" in result.output
    assert "Using latest full backup as baseline: test_db_20251015_full (full)" in result.output


def test_backup_incremental_with_specific_baseline(
    config_file,
    mock_db,
    mock_resolved_cluster,
    mock_healthy_cluster,
    mock_repo_exists,
    setup_password_env,
    mocker,
):
    """Test incremental backup with user-specified baseline."""
    runner = CliRunner()

    mocker.patch(
        "starrocks_br.planner.find_tables_by_group",
        return_value=[{"database": "test_db", "table": "fact_table"}],
    )
    mocker.patch(
        "starrocks_br.planner.find_recent_partitions",
        return_value=[
            {"database": "test_db", "table": "fact_table", "partition_name": "p20251016"}
        ],
    )
    mocker.patch("starrocks_br.labels.determine_backup_label", return_value="test_db_20251016_inc")
    mocker.patch(
        "starrocks_br.planner.build_incremental_backup_command",
        return_value="BACKUP DATABASE test_db SNAPSHOT test_db_20251016_inc TO test_repo",
    )
    mocker.patch("starrocks_br.concurrency.reserve_job_slot")
    mocker.patch("starrocks_br.planner.record_backup_partitions")
    mocker.patch(
        "starrocks_br.executor.execute_backup",
        return_value={
            "success": True,
            "final_status": {"state": "FINISHED"},
            "error_message": None,
        },
    )

    result = runner.invoke(
        cli.backup_incremental,
        [
            "--config",
            config_file,
            "--baseline-backup",
            "test_db_20251010_full",
            "--group",
            "daily_incremental",
        ],
    )

    assert result.exit_code == 0
    assert "Backup completed successfully" in result.output
    assert "Using specified baseline backup: test_db_20251010_full" in result.output


def test_backup_full_success(
    config_file,
    mock_db,
    mock_resolved_cluster,
    mock_healthy_cluster,
    mock_repo_exists,
    mock_validate_tables_exist,
    setup_password_env,
    mocker,
):
    """Test successful full backup."""
    runner = CliRunner()

    mocker.patch(
        "starrocks_br.planner.build_full_backup_command",
        return_value="BACKUP DATABASE test_db SNAPSHOT test_db_20251016_full TO test_repo",
    )
    mocker.patch(
        "starrocks_br.planner.find_tables_by_group",
        return_value=[{"database": "test_db", "table": "dim_customers"}],
    )
    mocker.patch("starrocks_br.planner.get_all_partitions_for_tables", return_value=[])
    mocker.patch("starrocks_br.labels.determine_backup_label", return_value="test_db_20251016_full")
    mocker.patch("starrocks_br.concurrency.reserve_job_slot")
    mocker.patch("starrocks_br.planner.record_backup_partitions")
    mocker.patch(
        "starrocks_br.executor.execute_backup",
        return_value={
            "success": True,
            "final_status": {"state": "FINISHED"},
            "error_message": None,
        },
    )

    result = runner.invoke(
        cli.backup_full, ["--config", config_file, "--group", "weekly_dimensions"]
    )

    assert result.exit_code == 0
    assert "Backup completed successfully" in result.output


def test_backup_full_resolves_group_name_to_id(
    config_file,
    mock_db,
    mock_resolved_cluster,
    mock_healthy_cluster,
    mock_repo_exists,
    mock_validate_tables_exist,
    setup_password_env,
    mocker,
):
    """The CLI keeps taking --group <name> but resolves it to an id before calling the command layer."""
    runner = CliRunner()

    resolve_mock = mocker.patch(
        "starrocks_br.inventory_groups.get_group_id_by_name", return_value=42
    )
    mocker.patch(
        "starrocks_br.planner.build_full_backup_command",
        return_value="BACKUP DATABASE test_db SNAPSHOT test_db_20251016_full TO test_repo",
    )
    find_tables_mock = mocker.patch(
        "starrocks_br.planner.find_tables_by_group",
        return_value=[{"database": "test_db", "table": "dim_customers"}],
    )
    mocker.patch("starrocks_br.planner.get_all_partitions_for_tables", return_value=[])
    mocker.patch("starrocks_br.labels.determine_backup_label", return_value="test_db_20251016_full")
    mocker.patch("starrocks_br.concurrency.reserve_job_slot")
    mocker.patch("starrocks_br.planner.record_backup_partitions")
    mocker.patch(
        "starrocks_br.executor.execute_backup",
        return_value={
            "success": True,
            "final_status": {"state": "FINISHED"},
            "error_message": None,
        },
    )

    result = runner.invoke(
        cli.backup_full, ["--config", config_file, "--group", "weekly_dimensions"]
    )

    assert result.exit_code == 0
    resolve_mock.assert_called_once_with(mocker.ANY, mocker.ANY, "weekly_dimensions")
    assert find_tables_mock.call_args[0][-1] == 42


def test_backup_full_with_unresolvable_group_name_fails_clearly(
    config_file,
    mock_db,
    mock_resolved_cluster,
    mock_healthy_cluster,
    mock_repo_exists,
    setup_password_env,
    mocker,
):
    """An unresolvable --group name fails before any job is created, with a clear error."""
    runner = CliRunner()

    mocker.patch(
        "starrocks_br.inventory_groups.get_group_id_by_name",
        side_effect=inventory_groups.InventoryGroupNotFoundError("Inventory group 'no_such_group' not found"),
    )
    execute_backup = mocker.patch("starrocks_br.executor.execute_backup")

    result = runner.invoke(
        cli.backup_full, ["--config", config_file, "--group", "no_such_group"]
    )

    assert result.exit_code != 0
    assert "not found" in result.output.lower()
    execute_backup.assert_not_called()


def test_backup_reserves_slot_before_recording_partitions(
    config_file,
    mock_db,
    mock_resolved_cluster,
    mock_healthy_cluster,
    mock_repo_exists,
    setup_password_env,
    mocker,
):
    """Test that backup reserves job slot before recording partitions (correct order)."""
    runner = CliRunner()
    call_order = []

    def mock_reserve_job_slot(*args, **kwargs):
        call_order.append("reserve_job_slot")

    def mock_record_backup_partitions(*args, **kwargs):
        call_order.append("record_backup_partitions")

    mocker.patch("starrocks_br.labels.determine_backup_label", return_value="test_backup")
    mocker.patch(
        "starrocks_br.planner.find_tables_by_group",
        return_value=[{"database": "test_db", "table": "fact_table"}],
    )
    mocker.patch(
        "starrocks_br.planner.find_latest_full_backup",
        return_value={
            "label": "test_db_20251015_full",
            "backup_type": "full",
            "finished_at": "2025-10-15 10:00:00",
        },
    )
    mocker.patch(
        "starrocks_br.planner.find_recent_partitions",
        return_value=[
            {"database": "test_db", "table": "fact_table", "partition_name": "p20251016"}
        ],
    )
    mocker.patch(
        "starrocks_br.planner.build_incremental_backup_command",
        return_value="BACKUP DATABASE test_db SNAPSHOT test_backup TO test_repo",
    )
    mocker.patch("starrocks_br.concurrency.reserve_job_slot", side_effect=mock_reserve_job_slot)
    mocker.patch(
        "starrocks_br.planner.record_backup_partitions", side_effect=mock_record_backup_partitions
    )
    mocker.patch(
        "starrocks_br.executor.execute_backup",
        return_value={
            "success": True,
            "final_status": {"state": "FINISHED"},
            "error_message": None,
        },
    )

    result = runner.invoke(cli.backup_incremental, ["--config", config_file, "--group", "daily"])

    assert result.exit_code == 0
    assert len(call_order) == 2
    assert call_order[0] == "reserve_job_slot"
    assert call_order[1] == "record_backup_partitions"
