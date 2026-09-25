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

import click

from . import exceptions


def display_structured_error(
    title: str,
    reason: str,
    what_to_do: list[str],
    inputs: dict = None,
    help_links: list[str] = None,
) -> None:
    click.echo()
    click.echo(click.style(f"❌ {title}", fg="red", bold=True), err=True)
    click.echo(reason, err=True)
    click.echo()

    click.echo(click.style("REASON", fg="yellow", bold=True), err=True)
    click.echo(reason, err=True)
    click.echo()

    click.echo(click.style("WHAT YOU CAN DO", fg="cyan", bold=True), err=True)
    for i, action in enumerate(what_to_do, 1):
        click.echo(f"{i}) {action}", err=True)
    click.echo()

    if inputs:
        click.echo(click.style("INPUT YOU PROVIDED", fg="magenta", bold=True), err=True)
        for key, value in inputs.items():
            if value is None:
                click.echo(f" Missing: {key}", err=True)
            else:
                click.echo(f" {key}: {value}", err=True)
        click.echo()

    if help_links:
        click.echo(click.style("NEED HELP?", fg="green", bold=True), err=True)
        for link in help_links:
            click.echo(f" → {link}", err=True)
        click.echo()


def handle_missing_option_error(exc: exceptions.MissingOptionError, config: str = None) -> None:
    display_structured_error(
        title="OPERATION FAILED",
        reason=f'The "{exc.missing_option}" option was not provided.\nThis parameter is required for the operation.',
        what_to_do=[
            f"Add the missing parameter: {exc.missing_option}",
            "Run the command with --help to see all required options",
            f"Example: starrocks-br <command> {exc.missing_option} <value>"
            + (f" --config {config}" if config else ""),
        ],
        inputs={"--config": config, "Missing": exc.missing_option}
        if config
        else {"Missing": exc.missing_option},
        help_links=["Run with --help for more information"],
    )


def handle_backup_label_not_found_error(
    exc: exceptions.BackupLabelNotFoundError, config: str = None
) -> None:
    display_structured_error(
        title="RESTORE FAILED",
        reason=f'The backup label "{exc.label}" does not exist in the repository'
        + (f' "{exc.repository}"' if exc.repository else "")
        + ",\nor the backup did not complete successfully.",
        what_to_do=[
            "Check the backup history recorded in this tool's own local database "
            "(no longer a StarRocks-side table - it lives in the SQLite metastore behind the API/CLI).",
            "Check whether the backup completed successfully using StarRocks SQL:"
            + (
                f"\n     SHOW BACKUP FROM `{exc.repository}`;"
                if exc.repository
                else "\n     SHOW BACKUP;"
            ),
            "Verify that the backup label spelling is correct.",
        ],
        inputs={"--config": config, "--target-label": exc.label, "Repository": exc.repository},
        help_links=["starrocks-br restore --help"],
    )


def handle_no_successful_full_backup_found_error(
    exc: exceptions.NoSuccessfulFullBackupFoundError, config: str = None
) -> None:
    display_structured_error(
        title="RESTORE FAILED",
        reason=f'No successful full backup was found before the incremental backup "{exc.incremental_label}".\nIncremental backups require a base full backup to restore from.',
        what_to_do=[
            "Verify that a full backup was created before this incremental backup by checking the "
            "backup history recorded in this tool's own local database (SQLite metastore).",
            "Run a full backup first:\n     starrocks-br backup full --config "
            + (config if config else "<config.yaml>")
            + " --group <group_name>",
            "Check that the full backup completed successfully before running the incremental backup",
        ],
        inputs={"--target-label": exc.incremental_label, "--config": config},
        help_links=["starrocks-br backup full --help"],
    )


def handle_table_not_found_in_backup_error(
    exc: exceptions.TableNotFoundInBackupError, config: str = None
) -> None:
    display_structured_error(
        title="TABLE NOT FOUND",
        reason=f'Table "{exc.table}" was not found in backup "{exc.label}" for database "{exc.database}".',
        what_to_do=[
            f"Check which tables were included in backup '{exc.label}' - the backup manifest is "
            "recorded in this tool's own local database (SQLite metastore), not on StarRocks.",
            "Verify the table name spelling is correct",
            f"Ensure the table was included in the backup {exc.label}",
        ],
        inputs={
            "--table": exc.table,
            "--target-label": exc.label,
            "Database": exc.database,
            "--config": config,
        },
        help_links=["starrocks-br restore --help"],
    )


def handle_invalid_table_name_error(exc: exceptions.InvalidTableNameError) -> None:
    display_structured_error(
        title="INVALID TABLE NAME",
        reason=f'The table name "{exc.table_name}" is invalid.\n{exc.reason}',
        what_to_do=[
            "Use only the table name without database prefix",
            "Example: Use 'my_table' instead of 'database.my_table'",
            "The database name should come from the config file",
        ],
        inputs={"--table": exc.table_name},
        help_links=["starrocks-br restore --help"],
    )


def handle_config_file_not_found_error(exc: exceptions.ConfigFileNotFoundError) -> None:
    display_structured_error(
        title="CONFIG FILE NOT FOUND",
        reason=f'The configuration file "{exc.config_path}" could not be found.',
        what_to_do=[
            "Verify the config file path is correct",
            "Ensure the file exists at the specified location",
            "Create a config file with the required settings:\n     host, port, user, database, repository",
        ],
        inputs={"--config": exc.config_path},
        help_links=["Check the documentation for config file format"],
    )


def handle_config_validation_error(
    exc: exceptions.ConfigValidationError, config: str = None
) -> None:
    display_structured_error(
        title="CONFIGURATION ERROR",
        reason=str(exc),
        what_to_do=[
            "Review your configuration file for missing or invalid settings",
            "Ensure all required fields are present: host, port, user, database, repository",
            "Check that values are in the correct format",
        ],
        inputs={"--config": config},
        help_links=["Check the documentation for config file requirements"],
    )


def handle_cluster_health_check_failed_error(
    exc: exceptions.ClusterHealthCheckFailedError, config: str = None
) -> None:
    display_structured_error(
        title="CLUSTER HEALTH CHECK FAILED",
        reason=f"The StarRocks cluster health check failed: {exc.health_message}",
        what_to_do=[
            "Check that the StarRocks cluster is running",
            "Verify database connectivity settings in your config file",
            "Check cluster status with: SHOW PROC '/frontends'; SHOW PROC '/backends';",
        ],
        inputs={"--config": config},
        help_links=["Check StarRocks documentation for troubleshooting cluster issues"],
    )


def handle_snapshot_not_found_error(
    exc: exceptions.SnapshotNotFoundError, config: str = None
) -> None:
    display_structured_error(
        title="SNAPSHOT NOT FOUND",
        reason=f'Snapshot "{exc.snapshot_name}" was not found in repository "{exc.repository}".',
        what_to_do=[
            f"List available snapshots:\n     SHOW SNAPSHOT ON {exc.repository};",
            "Verify the snapshot name spelling is correct",
            "Ensure the backup completed successfully - check the backup history recorded in "
            "this tool's own local database (SQLite metastore).",
        ],
        inputs={"Snapshot": exc.snapshot_name, "Repository": exc.repository, "--config": config},
        help_links=["starrocks-br restore --help"],
    )


def handle_no_partitions_found_error(
    exc: exceptions.NoPartitionsFoundError, config: str = None, group: str = None
) -> None:
    display_structured_error(
        title="NO PARTITIONS FOUND",
        reason="No partitions were found to backup"
        + (f" for group '{exc.group_name}'" if exc.group_name else "")
        + ".",
        what_to_do=[
            "Verify that the inventory group exists and has table memberships - inventory "
            "groups are tracked in this tool's own local database (SQLite metastore), not on StarRocks.",
            "Check that the tables in the group have partitions",
            "Ensure the baseline backup date is correct",
        ],
        inputs={"--group": exc.group_name or group, "--config": config},
        help_links=["starrocks-br backup incremental --help"],
    )


def handle_no_tables_found_error(
    exc: exceptions.NoTablesFoundError, config: str = None, target_label: str = None
) -> None:
    display_structured_error(
        title="NO TABLES FOUND",
        reason="No tables were found"
        + (
            f" in backup '{exc.label}' for group '{exc.group}'"
            if exc.group and exc.label
            else f" in backup '{exc.label}'"
            if exc.label
            else ""
        )
        + ".",
        what_to_do=[
            "Verify that tables exist in the backup manifest - it is recorded in this tool's "
            "own local database (SQLite metastore), not on StarRocks.",
            "Check that the inventory group name is correct"
            if exc.group
            else "Verify the backup completed successfully",
            "List available backups by checking the backup history in this tool's own local database.",
        ],
        inputs={
            "--target-label": exc.label or target_label,
            "--group": exc.group,
            "--config": config,
        },
        help_links=["starrocks-br restore --help"],
    )


def handle_restore_operation_cancelled_error() -> None:
    display_structured_error(
        title="OPERATION CANCELLED",
        reason="The restore operation was cancelled by the user.",
        what_to_do=[
            "Review the restore plan carefully",
            "Run again and confirm with 'Y' to proceed",
            "Use the --yes flag to skip the confirmation prompt:\n     starrocks-br restore --yes ...",
        ],
        help_links=["starrocks-br restore --help"],
    )


def handle_concurrency_conflict_error(
    exc: exceptions.ConcurrencyConflictError, config: str = None
) -> None:
    active_job_strings = [f"{job[0]}:{job[1]}" for job in exc.active_jobs]
    first_label = exc.active_labels[0] if exc.active_labels else "unknown"

    display_structured_error(
        title="CONCURRENCY CONFLICT",
        reason=f"Another '{exc.scope}' job is already running.\nOnly one job of the same type can run at a time to prevent conflicts.",
        what_to_do=[
            f"Wait for the active job to complete: {', '.join(active_job_strings)}",
            f"Check the status of job '{first_label}' - job locks are tracked in this tool's "
            "own local database (SQLite metastore), not on StarRocks.",
            "If the job is stuck, it will self-heal on the next run once the underlying "
            "StarRocks backup is confirmed no longer active.",
            "Verify the job is not actually running in StarRocks before assuming it is stuck",
        ],
        inputs={
            "--config": config,
            "Scope": exc.scope,
            "Active jobs": ", ".join(active_job_strings),
        },
        help_links=["Job locks live in this tool's local database, scoped per cluster"],
    )


def handle_no_full_backup_found_error(
    exc: exceptions.NoFullBackupFoundError, config: str = None, group: str = None
) -> None:
    display_structured_error(
        title="NO FULL BACKUP FOUND",
        reason=f"No successful full backup was found for database '{exc.database}'.\nIncremental backups require a baseline full backup to compare against.",
        what_to_do=[
            "Run a full backup first:\n     starrocks-br backup full --config "
            + (config if config else "<config.yaml>")
            + f" --group {group if group else '<group_name>'}",
            "Verify no full backups exist for this database by checking the backup history "
            "recorded in this tool's own local database (SQLite metastore).",
            "After the full backup completes successfully, retry the incremental backup",
        ],
        inputs={"Database": exc.database, "--config": config, "--group": group},
        help_links=["starrocks-br backup full --help"],
    )


def handle_invalid_tables_in_inventory_error(
    exc: exceptions.InvalidTablesInInventoryError, config: str = None
) -> None:
    invalid_tables_str = ", ".join(f"'{t}'" for t in exc.invalid_tables)

    display_structured_error(
        title="INVALID TABLES IN INVENTORY",
        reason=f"The following table(s) in the inventory do not exist in database '{exc.database}':\n{invalid_tables_str}\n\nThese tables are referenced in the table inventory but cannot be found in the actual database.",
        what_to_do=[
            "Remove the invalid table(s) from the inventory group - table inventory is tracked "
            "in this tool's own local database (SQLite metastore), not on StarRocks.",
            "Verify the table names are spelled correctly in the inventory",
            f"Check which tables exist in the database:\n     SHOW TABLES FROM `{exc.database}`;",
            "Update the inventory entry with the correct table name once you know it",
        ],
        inputs={
            "Database": exc.database,
            "Invalid tables": invalid_tables_str,
            "Group": exc.group,
            "--config": config,
        },
        help_links=[
            "Inventory groups live in this tool's local database, scoped per cluster",
            "Run 'SHOW TABLES' to see available tables",
        ],
    )
