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

"""Backend-agnostic job handlers.

Each handler builds a StarRocksDB connection from a Cluster row and runs
exactly the same sequence of core-library calls that the equivalent `cli.py`
command runs (see design.md Decision 3 / specs/api-job-execution
"Job execution reuses existing backup/restore/prune behavior unchanged").
A handler takes no dependency on HTTP, threads, or SQLAlchemy - any
JobBackend (in-process thread today, an out-of-process worker later) can
call it the same way.
"""

from collections.abc import Callable
from typing import Any

from .. import (
    concurrency,
    executor,
    health,
    labels,
    planner,
    prune,
    repository,
    restore,
    schema,
)
from .. import (
    db as db_module,
)
from ..store.crypto import decrypt_password
from ..store.models import Cluster

OnProgress = Callable[[dict], None] | None


class OpsSchemaNotInitializedError(RuntimeError):
    pass


def _connect(cluster: Cluster) -> db_module.StarRocksDB:
    return db_module.StarRocksDB(
        host=cluster.host,
        port=cluster.port,
        user=cluster.user,
        password=decrypt_password(cluster.password_encrypted),
        database=cluster.database,
    )


def _ensure_ready(database: db_module.StarRocksDB, cluster: Cluster) -> None:
    was_created = schema.ensure_ops_schema(database, ops_database=cluster.ops_database)
    if was_created:
        raise OpsSchemaNotInitializedError(
            f"ops schema was auto-created for cluster '{cluster.name}'; "
            "run the equivalent of `starrocks-br init` for it first"
        )

    healthy, message = health.check_cluster_health(database)
    if not healthy:
        raise RuntimeError(f"Cluster health check failed: {message}")

    repository.ensure_repository(database, cluster.repository)


def run_backup_full(cluster: Cluster, params: dict[str, Any], on_progress: OnProgress = None) -> dict:
    group = params["group"]
    name = params.get("name")

    database = _connect(cluster)
    with database:
        _ensure_ready(database, cluster)

        label = labels.determine_backup_label(
            db=database,
            backup_type="full",
            database_name=cluster.database,
            custom_name=name,
            ops_database=cluster.ops_database,
        )

        tables = planner.find_tables_by_group(database, group, cluster.ops_database)
        planner.validate_tables_exist(database, cluster.database, tables, group)

        backup_command = planner.build_full_backup_command(
            database,
            group,
            cluster.repository,
            label,
            cluster.database,
            ops_database=cluster.ops_database,
        )
        if not backup_command:
            raise RuntimeError(
                f"No tables found in group '{group}' for database '{cluster.database}' to backup"
            )

        all_partitions = planner.get_all_partitions_for_tables(database, cluster.database, tables)

        concurrency.reserve_job_slot(
            database, scope="backup", label=label, ops_database=cluster.ops_database
        )
        planner.record_backup_partitions(
            database, label, all_partitions, ops_database=cluster.ops_database
        )

        result = executor.execute_backup(
            database,
            backup_command,
            repository=cluster.repository,
            backup_type="full",
            scope="backup",
            database=cluster.database,
            ops_database=cluster.ops_database,
            on_progress=on_progress,
        )

        if not result["success"]:
            raise RuntimeError(result["error_message"])

        return {"label": label, "final_status": result["final_status"]}


def run_backup_incremental(
    cluster: Cluster, params: dict[str, Any], on_progress: OnProgress = None
) -> dict:
    group = params["group"]
    name = params.get("name")
    baseline_backup = params.get("baseline_backup")

    database = _connect(cluster)
    with database:
        _ensure_ready(database, cluster)

        label = labels.determine_backup_label(
            db=database,
            backup_type="incremental",
            database_name=cluster.database,
            custom_name=name,
            ops_database=cluster.ops_database,
        )

        partitions = planner.find_recent_partitions(
            database,
            cluster.database,
            baseline_backup_label=baseline_backup,
            group_name=group,
            ops_database=cluster.ops_database,
        )
        if not partitions:
            raise RuntimeError("No partitions found to backup")

        backup_command = planner.build_incremental_backup_command(
            partitions, cluster.repository, label, cluster.database
        )

        concurrency.reserve_job_slot(
            database, scope="backup", label=label, ops_database=cluster.ops_database
        )
        planner.record_backup_partitions(
            database, label, partitions, ops_database=cluster.ops_database
        )

        result = executor.execute_backup(
            database,
            backup_command,
            repository=cluster.repository,
            backup_type="incremental",
            scope="backup",
            database=cluster.database,
            ops_database=cluster.ops_database,
            on_progress=on_progress,
        )

        if not result["success"]:
            raise RuntimeError(result["error_message"])

        return {"label": label, "final_status": result["final_status"]}


def run_restore(cluster: Cluster, params: dict[str, Any], on_progress: OnProgress = None) -> dict:
    target_label = params["target_label"]
    group = params.get("group")
    table = params.get("table")
    rename_suffix = params.get("rename_suffix") or "_restored"

    if group and table:
        raise ValueError("Cannot specify both 'group' and 'table'")

    database = _connect(cluster)
    with database:
        _ensure_ready(database, cluster)

        restore_pair = restore.find_restore_pair(
            database, target_label, ops_database=cluster.ops_database
        )

        tables_to_restore = restore.get_tables_from_backup(
            database,
            target_label,
            group=group,
            table=table,
            database=cluster.database if table else None,
            ops_database=cluster.ops_database,
        )
        if not tables_to_restore:
            raise RuntimeError(f"No tables found to restore for backup '{target_label}'")

        result = restore.execute_restore_flow(
            database,
            cluster.repository,
            restore_pair,
            tables_to_restore,
            rename_suffix,
            skip_confirmation=True,
            ops_database=cluster.ops_database,
            on_progress=on_progress,
        )

        if not result["success"]:
            raise RuntimeError(result["error_message"])

        return {"restore_pair": restore_pair, "tables": tables_to_restore, "message": result.get("message")}


def run_prune(cluster: Cluster, params: dict[str, Any], on_progress: OnProgress = None) -> dict:
    del on_progress  # prune has no long-running per-snapshot progress to report

    group = params.get("group")
    keep_last = params.get("keep_last")
    older_than = params.get("older_than")
    snapshot = params.get("snapshot")
    snapshots = params.get("snapshots")
    dry_run = bool(params.get("dry_run"))

    specified = [opt for opt in (keep_last, older_than, snapshot, snapshots) if opt is not None]
    if len(specified) != 1:
        raise ValueError(
            "Must specify exactly one of: keep_last, older_than, snapshot, snapshots"
        )

    database = _connect(cluster)
    with database:
        _ensure_ready(database, cluster)

        if keep_last:
            strategy, kwargs = "keep_last", {"count": keep_last}
        elif older_than:
            strategy, kwargs = "older_than", {"timestamp": older_than}
        elif snapshot:
            strategy, kwargs = "specific", {"snapshot": snapshot}
        else:
            snapshot_list = [s.strip() for s in snapshots.split(",")]
            strategy, kwargs = "multiple", {"snapshots": snapshot_list}

        all_backups = prune.get_successful_backups(
            database, cluster.repository, group=group, ops_database=cluster.ops_database
        )
        if not all_backups:
            return {"deleted": [], "kept_count": 0}

        if strategy in ("specific", "multiple"):
            for snap in kwargs.get("snapshots", [kwargs.get("snapshot")]):
                prune.verify_snapshot_exists(database, cluster.repository, snap)

        snapshots_to_delete = prune.filter_snapshots_to_delete(all_backups, strategy, **kwargs)

        if dry_run or not snapshots_to_delete:
            return {
                "deleted": [],
                "would_delete": [s["label"] for s in snapshots_to_delete],
                "kept_count": len(all_backups) - len(snapshots_to_delete),
            }

        deleted = []
        for snap in snapshots_to_delete:
            prune.execute_drop_snapshot(database, cluster.repository, snap["label"])
            prune.cleanup_backup_history(database, snap["label"], ops_database=cluster.ops_database)
            deleted.append(snap["label"])

        return {"deleted": deleted, "kept_count": len(all_backups) - len(deleted)}


JOB_HANDLERS: dict[str, Callable[[Cluster, dict[str, Any], OnProgress], dict]] = {
    "backup_full": run_backup_full,
    "backup_incremental": run_backup_incremental,
    "restore": run_restore,
    "prune": run_prune,
}
