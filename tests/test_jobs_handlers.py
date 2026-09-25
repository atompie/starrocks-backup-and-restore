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

from starrocks_br.jobs import handlers
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
        ops_database="ops",
        default_backend="thread",
    )


@pytest.fixture
def mock_decrypt(mocker):
    return mocker.patch("starrocks_br.jobs.handlers.decrypt_password", return_value="plain-pw")


def test_run_backup_full_builds_same_command_as_cli(
    cluster, mock_decrypt, mock_db, mock_initialized_schema, mock_healthy_cluster, mock_repo_exists, mocker
):
    """The handler must produce the identical backup command/label the CLI would for equivalent inputs."""
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

    result = handlers.run_backup_full(cluster, {"group": "production"})

    assert result == {"label": "test_db_20251016_full", "final_status": {"state": "FINISHED"}}
    execute_backup.assert_called_once()
    _, kwargs = execute_backup.call_args
    assert kwargs["backup_type"] == "full"
    assert kwargs["repository"] == "test_repo"
    assert kwargs["ops_database"] == "ops"
    called_command = execute_backup.call_args[0][1]
    assert called_command == "BACKUP DATABASE test_db SNAPSHOT test_db_20251016_full TO test_repo"


def test_run_backup_full_raises_on_ops_schema_not_initialized(
    cluster, mock_decrypt, mock_db, mock_uninitialized_schema
):
    with pytest.raises(handlers.OpsSchemaNotInitializedError):
        handlers.run_backup_full(cluster, {"group": "production"})


def test_run_backup_full_raises_on_unhealthy_cluster(
    cluster, mock_decrypt, mock_db, mock_initialized_schema, mock_unhealthy_cluster
):
    with pytest.raises(RuntimeError, match="health check failed"):
        handlers.run_backup_full(cluster, {"group": "production"})


def test_run_backup_full_propagates_execute_backup_failure(
    cluster, mock_decrypt, mock_db, mock_initialized_schema, mock_healthy_cluster, mock_repo_exists, mocker
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

    with pytest.raises(RuntimeError, match="boom"):
        handlers.run_backup_full(cluster, {"group": "production"})


def test_run_backup_incremental_passes_baseline_and_progress_callback(
    cluster, mock_decrypt, mock_db, mock_initialized_schema, mock_healthy_cluster, mock_repo_exists, mocker
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
    handlers.run_backup_incremental(
        cluster, {"group": "production", "baseline_backup": "base_lbl"}, on_progress=progress_cb
    )

    assert execute_backup.call_args.kwargs["on_progress"] is progress_cb


def test_run_restore_rejects_group_and_table_together(cluster, mock_decrypt):
    with pytest.raises(ValueError, match="Cannot specify both"):
        handlers.run_restore(cluster, {"target_label": "lbl", "group": "g", "table": "t"})


def test_run_restore_delegates_to_execute_restore_flow(
    cluster, mock_decrypt, mock_db, mock_initialized_schema, mock_healthy_cluster, mock_repo_exists, mocker
):
    mocker.patch("starrocks_br.restore.find_restore_pair", return_value=["full_lbl"])
    mocker.patch("starrocks_br.restore.get_tables_from_backup", return_value=["d.t"])
    execute_flow = mocker.patch(
        "starrocks_br.restore.execute_restore_flow",
        return_value={"success": True, "message": "done"},
    )

    result = handlers.run_restore(cluster, {"target_label": "full_lbl"})

    assert result["restore_pair"] == ["full_lbl"]
    assert result["tables"] == ["d.t"]
    execute_flow.assert_called_once()
    assert execute_flow.call_args.kwargs["skip_confirmation"] is True


def test_run_prune_requires_exactly_one_strategy(cluster, mock_decrypt):
    with pytest.raises(ValueError, match="exactly one"):
        handlers.run_prune(cluster, {})


def test_run_prune_deletes_matching_snapshots(
    cluster, mock_decrypt, mock_db, mock_initialized_schema, mock_healthy_cluster, mock_repo_exists, mocker
):
    mocker.patch(
        "starrocks_br.prune.get_successful_backups",
        return_value=[{"label": "a"}, {"label": "b"}],
    )
    mocker.patch(
        "starrocks_br.prune.filter_snapshots_to_delete", return_value=[{"label": "a"}]
    )
    drop = mocker.patch("starrocks_br.prune.execute_drop_snapshot")
    cleanup = mocker.patch("starrocks_br.prune.cleanup_backup_history")

    result = handlers.run_prune(cluster, {"keep_last": 1})

    assert result == {"deleted": ["a"], "kept_count": 1}
    drop.assert_called_once_with(mock_db, "test_repo", "a")
    cleanup.assert_called_once_with(mock_db, "a", ops_database="ops")


def test_job_handlers_map_has_all_four_types():
    assert set(handlers.JOB_HANDLERS) == {
        "backup_full",
        "backup_incremental",
        "restore",
        "prune",
    }
