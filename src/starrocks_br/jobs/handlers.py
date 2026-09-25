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
A handler takes no dependency on HTTP or threads; any JobBackend (in-process
thread today, an out-of-process worker later) can call it the same way.

Ops-table access (table_inventory, backup_history, restore_history,
run_status, backup_partitions - now SQLite-backed, scoped by cluster_id)
uses short-lived `session_scope()` blocks opened right around each group of
reads/writes, never spanning a StarRocks submit-and-poll operation. See
design.md's move-ops-tables-to-sqlite Decision 2: a future non-thread-based
job backend may run many jobs concurrently, and SQLite allows only one
writer at a time, so a session held open across a multi-minute poll loop
would block every other job's ops-table access for that whole duration.
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
)
from .. import (
    db as db_module,
)
from ..store.crypto import decrypt_password
from ..store.models import Cluster
from ..store.session import session_scope

OnProgress = Callable[[dict], None] | None


def _connect(cluster: Cluster) -> db_module.StarRocksDB:
    return db_module.StarRocksDB(
        host=cluster.host,
        port=cluster.port,
        user=cluster.user,
        password=decrypt_password(cluster.password_encrypted),
        database=cluster.database,
    )


def _ensure_ready(database: db_module.StarRocksDB, cluster: Cluster) -> None:
    healthy, message = health.check_cluster_health(database)
    if not healthy:
        raise RuntimeError(f"Cluster health check failed: {message}")

    repository.ensure_repository(database, cluster.repository)


def run_backup_full(cluster: Cluster, params: dict[str, Any], on_progress: OnProgress = None) -> dict:
    group = params.get("group")
    if not group:
        raise ValueError("'group' is required for backup_full")
    name = params.get("name")

    database = _connect(cluster)
    with database:
        _ensure_ready(database, cluster)

        with session_scope() as session:
            label = labels.determine_backup_label(
                session, cluster.id, "full", cluster.database, custom_name=name
            )

            tables = planner.find_tables_by_group(session, cluster.id, group)
            planner.validate_tables_exist(database, cluster.database, tables, group)

            backup_command = planner.build_full_backup_command(
                session, cluster.id, group, cluster.repository, label, cluster.database
            )
            if not backup_command:
                raise RuntimeError(
                    f"No tables found in group '{group}' for database '{cluster.database}' to backup"
                )

            all_partitions = planner.get_all_partitions_for_tables(database, cluster.database, tables)

            concurrency.reserve_job_slot(database, session, cluster.id, "backup", label)
            planner.record_backup_partitions(session, cluster.id, label, all_partitions)

        with session_scope() as session:
            result = executor.execute_backup(
                database,
                session,
                cluster.id,
                backup_command,
                repository=cluster.repository,
                backup_type="full",
                scope="backup",
                database=cluster.database,
                on_progress=on_progress,
            )

        if not result["success"]:
            raise RuntimeError(result["error_message"])

        return {"label": label, "final_status": result["final_status"]}


def run_backup_incremental(
    cluster: Cluster, params: dict[str, Any], on_progress: OnProgress = None
) -> dict:
    group = params.get("group")
    if not group:
        raise ValueError("'group' is required for backup_incremental")
    name = params.get("name")
    baseline_backup = params.get("baseline_backup")

    database = _connect(cluster)
    with database:
        _ensure_ready(database, cluster)

        with session_scope() as session:
            label = labels.determine_backup_label(
                session, cluster.id, "incremental", cluster.database, custom_name=name
            )

            partitions = planner.find_recent_partitions(
                database,
                session,
                cluster.id,
                cluster.database,
                baseline_backup_label=baseline_backup,
                group_name=group,
            )
            if not partitions:
                raise RuntimeError("No partitions found to backup")

            backup_command = planner.build_incremental_backup_command(
                partitions, cluster.repository, label, cluster.database
            )

            concurrency.reserve_job_slot(database, session, cluster.id, "backup", label)
            planner.record_backup_partitions(session, cluster.id, label, partitions)

        with session_scope() as session:
            result = executor.execute_backup(
                database,
                session,
                cluster.id,
                backup_command,
                repository=cluster.repository,
                backup_type="incremental",
                scope="backup",
                database=cluster.database,
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

        with session_scope() as session:
            restore_pair = restore.find_restore_pair(session, cluster.id, target_label)

            tables_to_restore = restore.get_tables_from_backup(
                database,
                session,
                cluster.id,
                target_label,
                group=group,
                table=table,
                database=cluster.database if table else None,
            )
        if not tables_to_restore:
            raise RuntimeError(f"No tables found to restore for backup '{target_label}'")

        with session_scope() as session:
            result = restore.execute_restore_flow(
                database,
                session,
                cluster.id,
                cluster.repository,
                restore_pair,
                tables_to_restore,
                rename_suffix,
                skip_confirmation=True,
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

        # No long-running submit-and-poll operation here (DROP SNAPSHOT is a
        # single synchronous call per snapshot), so one session for the whole
        # handler body is fine - unlike backup/restore, there's no multi-minute
        # StarRocks wait to avoid holding a write lock across.
        with session_scope() as session:
            all_backups = prune.get_successful_backups(session, cluster.id, cluster.repository, group=group)
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
                prune.cleanup_backup_history(session, cluster.id, snap["label"])
                deleted.append(snap["label"])

            return {"deleted": deleted, "kept_count": len(all_backups) - len(deleted)}


JOB_HANDLERS: dict[str, Callable[[Cluster, dict[str, Any], OnProgress], dict]] = {
    "backup_full": run_backup_full,
    "backup_incremental": run_backup_incremental,
    "restore": run_restore,
    "prune": run_prune,
}
