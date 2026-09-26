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


class StarRocksBRError(Exception):
    pass


class MissingOptionError(StarRocksBRError):
    def __init__(self, missing_option: str):
        self.missing_option = missing_option
        super().__init__(f"Missing required option: {missing_option}")


class BackupLabelNotFoundError(StarRocksBRError):
    def __init__(self, label: str, repository: str = None):
        self.label = label
        self.repository = repository
        if repository:
            super().__init__(f"Backup label '{label}' not found in repository '{repository}'")
        else:
            super().__init__(f"Backup label '{label}' not found")


class NoSuccessfulFullBackupFoundError(StarRocksBRError):
    def __init__(self, incremental_label: str):
        self.incremental_label = incremental_label
        super().__init__(
            f"No successful full backup found before incremental '{incremental_label}'"
        )


class TableNotFoundInBackupError(StarRocksBRError):
    def __init__(self, table: str, label: str, database: str):
        self.table = table
        self.label = label
        self.database = database
        super().__init__(f"Table '{table}' not found in backup '{label}' for database '{database}'")


class InvalidTableNameError(StarRocksBRError):
    def __init__(self, table_name: str, reason: str):
        self.table_name = table_name
        self.reason = reason
        super().__init__(f"Invalid table name '{table_name}': {reason}")


class ConfigFileNotFoundError(StarRocksBRError):
    def __init__(self, config_path: str):
        self.config_path = config_path
        super().__init__(f"Config file not found: {config_path}")


class ConfigValidationError(StarRocksBRError):
    def __init__(self, message: str):
        super().__init__(f"Configuration error: {message}")


class ClusterNotInitializedError(StarRocksBRError):
    def __init__(self, identity: str):
        self.identity = identity
        super().__init__(
            f"No cluster registered for '{identity}' in the local SQLite metastore. "
            "Run 'starrocks-br init --config <config.yaml>' first."
        )


class ClusterHealthCheckFailedError(StarRocksBRError):
    def __init__(self, message: str):
        self.health_message = message
        super().__init__(f"Cluster health check failed: {message}")


class SnapshotNotFoundError(StarRocksBRError):
    def __init__(self, snapshot_name: str, repository: str):
        self.snapshot_name = snapshot_name
        self.repository = repository
        super().__init__(f"Snapshot '{snapshot_name}' not found in repository '{repository}'")


class NoPartitionsFoundError(StarRocksBRError):
    def __init__(self, group_name: str = None):
        self.group_name = group_name
        if group_name:
            super().__init__(f"No partitions found to backup for group '{group_name}'")
        else:
            super().__init__("No partitions found to backup")


class NoTablesFoundError(StarRocksBRError):
    def __init__(self, group: int | None = None, label: str = None):
        self.group = group
        self.label = label
        if group and label:
            super().__init__(f"No tables found in backup '{label}' for group '{group}'")
        elif group:
            super().__init__(f"No tables found for group '{group}'")
        elif label:
            super().__init__(f"No tables found in backup '{label}'")
        else:
            super().__init__("No tables found")


class RestoreOperationCancelledError(StarRocksBRError):
    def __init__(self):
        super().__init__("Restore operation cancelled by user")


class ConcurrencyConflictError(StarRocksBRError):
    def __init__(self, scope: str, active_jobs: list[tuple[str, str, str]]):
        self.scope = scope
        self.active_jobs = active_jobs
        self.active_labels = [job[1] for job in active_jobs]
        super().__init__(
            f"Concurrency conflict: Another '{scope}' job is already active: {', '.join(f'{job[0]}:{job[1]}' for job in active_jobs)}"
        )


class NoFullBackupFoundError(StarRocksBRError):
    def __init__(self, database: str):
        self.database = database
        super().__init__(f"No successful full backup found for database '{database}'")


class InvalidTablesInInventoryError(StarRocksBRError):
    def __init__(self, database: str, invalid_tables: list[str], group: int | None = None):
        self.database = database
        self.invalid_tables = invalid_tables
        self.group = group
        tables_str = ", ".join(f"'{t}'" for t in invalid_tables)
        if group:
            super().__init__(
                f"Invalid tables in inventory group '{group}' for database '{database}': {tables_str}"
            )
        else:
            super().__init__(f"Invalid tables for database '{database}': {tables_str}")


class SnapshotAlreadyExistsError(StarRocksBRError):
    def __init__(self, snapshot_name: str):
        self.snapshot_name = snapshot_name
        super().__init__(f"Snapshot '{snapshot_name}' already exists in repository")


class BackupExecutionError(StarRocksBRError):
    def __init__(self, message: str, final_status: dict | None = None):
        self.final_status = final_status or {}
        super().__init__(message)


class ClusterHasActiveJobError(StarRocksBRError):
    def __init__(self, cluster_id: int):
        self.cluster_id = cluster_id
        super().__init__("Cluster has a job in PENDING or RUNNING state; cannot delete")


class ClusterHasEnabledScheduleError(StarRocksBRError):
    def __init__(self, cluster_id: int):
        self.cluster_id = cluster_id
        super().__init__(
            "Cluster has an enabled schedule; disable or delete it before removing the cluster"
        )


class RepositoryAlreadyExistsError(StarRocksBRError):
    def __init__(self, name: str, cluster_name: str):
        self.name = name
        self.cluster_name = cluster_name
        super().__init__(f"Repository '{name}' already exists on cluster '{cluster_name}'")


class RepositoryStillHasSnapshotsError(StarRocksBRError):
    def __init__(self, name: str):
        self.name = name
        super().__init__(f"Repository '{name}' still holds snapshot data; cannot delete")


class RestoreExecutionError(StarRocksBRError):
    def __init__(self, message: str):
        super().__init__(message)


class InvalidCadenceError(StarRocksBRError):
    def __init__(self, cadence: str, reason: str):
        self.cadence = cadence
        self.reason = reason
        super().__init__(f"Invalid cadence expression '{cadence}': {reason}")
