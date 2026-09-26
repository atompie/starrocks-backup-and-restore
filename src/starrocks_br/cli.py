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

import os
import sys

import click
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import commands, db, error_handler, exceptions, inventory_groups, logger, repository
from . import config as config_module
from .store.crypto import encrypt_password
from .store.models import Cluster
from .store.session import session_scope

try:
    from .cli_api import api_group
except ImportError:
    api_group = None


def resolve_cluster(session: Session, cfg: dict, *, create: bool) -> Cluster:
    """Get-or-create the `Cluster` row this config resolves to.

    Ops bookkeeping (table_inventory, backup_history, restore_history,
    run_status, backup_partitions) now lives in the SQLite metastore, keyed
    by `cluster_id` - the legacy YAML-driven CLI has no cluster-identity
    concept of its own, so one is derived from the config (see
    `config.get_cluster_identity`).

    `init` is the only command allowed to create the row (`create=True`);
    every other command requires it to already exist (`create=False`) and
    raises `ClusterNotInitializedError` otherwise - this preserves the
    tool's existing stricter "must init first" behavior and guards against
    a `--config` typo silently registering a phantom cluster.

    On every run, mutable connection fields (host/port/user/password) are
    refreshed from the YAML so the config file stays authoritative; the
    stored identity (`name`) itself is left alone unless the config's
    optional `name` field is explicitly present and differs from what's
    stored. `database`/`repository` are no longer stored on `Cluster` -
    each command passes its config's `database`/`repository` through in
    the operation's own params instead (see openspec/changes/decouple-
    database-and-repository-from-cluster).
    """
    identity = config_module.get_cluster_identity(cfg)
    password = os.getenv("STARROCKS_PASSWORD", "")

    cluster = session.scalars(select(Cluster).where(Cluster.name == identity)).one_or_none()

    if cluster is None:
        if not create:
            raise exceptions.ClusterNotInitializedError(identity)
        cluster = Cluster(
            name=identity,
            host=cfg["host"],
            port=cfg["port"],
            user=cfg["user"],
            password_encrypted=encrypt_password(password),
        )
        session.add(cluster)
        session.flush()
        return cluster

    cluster.host = cfg["host"]
    cluster.port = cfg["port"]
    cluster.user = cfg["user"]
    cluster.password_encrypted = encrypt_password(password)
    configured_name = cfg.get("name")
    if configured_name and configured_name != cluster.name:
        cluster.name = configured_name
    session.flush()
    return cluster


def _handle_snapshot_exists_error(
    error_details: dict,
    label: str,
    config: str,
    repository: str,
    backup_type: str,
    group: str,
    baseline_backup: str = None,
) -> None:
    """Handle snapshot_exists error by providing helpful guidance to the user.

    Args:
        error_details: Error details dict containing error_type and snapshot_name
        label: The backup label that was generated
        config: Path to config file
        repository: Repository name
        backup_type: Type of backup ('incremental' or 'full')
        group: Inventory group name
        baseline_backup: Optional baseline backup label (for incremental backups)
    """
    snapshot_name = error_details.get("snapshot_name", label)
    logger.error(f"Snapshot '{snapshot_name}' already exists in the repository.")
    logger.info("")
    logger.info("This typically happens when:")
    logger.info("  • The CLI lost connectivity during a previous backup operation")
    logger.info("  • The backup completed on the server, but backup_history wasn't updated")
    logger.info("")
    logger.info("To resolve this, retry the backup with a custom label using --name:")

    if backup_type == "incremental":
        retry_cmd = f"  starrocks-br backup incremental --config {config} --group {group} --name {snapshot_name}_retry"
        if baseline_backup:
            retry_cmd += f" --baseline-backup {baseline_backup}"
        logger.info(retry_cmd)
    else:
        logger.info(
            f"  starrocks-br backup full --config {config} --group {group} --name {snapshot_name}_retry"
        )

    logger.info("")
    logger.tip("You can verify the existing backup by checking the repository or running:")
    logger.tip(f"  SHOW SNAPSHOT ON {repository} WHERE Snapshot = '{snapshot_name}'")


@click.group()
@click.option("--verbose", is_flag=True, help="Enable verbose debug logging")
@click.pass_context
def cli(ctx, verbose):
    """StarRocks Backup & Restore automation tool."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose

    if verbose:
        import logging

        logger.setup_logging(level=logging.DEBUG)
        logger.debug("Verbose logging enabled")
    else:
        logger.setup_logging()


@cli.command("init")
@click.option("--config", required=True, help="Path to config YAML file")
def init(config):
    """Register this cluster in the local SQLite metastore and bootstrap its inventory.

    Ops bookkeeping (table_inventory, backup_history, restore_history,
    run_status, backup_partitions) lives in a SQLite metastore shared with
    the API server, keyed by cluster_id - not in a StarRocks-side database.
    This command requires `alembic upgrade head` (against
    STARROCKS_BR_DATABASE_URL) to have already been run.

    Run this once before using backup/restore commands.
    """
    try:
        cfg = config_module.load_config(config)
        config_module.validate_config(cfg)

        table_inventory_entries = config_module.get_table_inventory_entries(cfg)

        database = db.StarRocksDB(
            host=cfg["host"],
            port=cfg["port"],
            user=cfg["user"],
            password=os.getenv("STARROCKS_PASSWORD"),
            database=cfg["database"],
            tls_config=cfg.get("tls"),
        )

        with database:
            logger.info("Validating repository...")
            repository.ensure_repository(database, cfg["repository"])
            logger.info("")

        logger.info("Registering cluster in the local SQLite metastore...")
        with session_scope() as session:
            cluster = resolve_cluster(session, cfg, create=True)
            if table_inventory_entries:
                inventory_groups.bootstrap_table_inventory(session, cluster.id, table_inventory_entries)
        logger.success("Cluster registered")
        logger.info("")

        if table_inventory_entries:
            logger.success(
                f"Table inventory bootstrapped from config with {len(table_inventory_entries)} entries"
            )
            logger.info("")
            logger.info("Next steps:")
            logger.info("1. Run your first backup:")
            logger.info(
                f"   starrocks-br backup incremental --group <your_group_name> --config {config}"
            )
        else:
            logger.info("Next steps:")
            logger.info(
                "1. Populate your table inventory (no rows exist yet) - add table_inventory "
                "entries under this config's 'table_inventory' YAML section and re-run init, "
                "or use the API's inventory-group endpoints."
            )
            logger.info("2. Run your first backup:")
            logger.info(
                "   starrocks-br backup incremental --group my_daily_incremental --config config.yaml"
            )

    except exceptions.ConfigFileNotFoundError as e:
        error_handler.handle_config_file_not_found_error(e)
        sys.exit(1)
    except exceptions.ConfigValidationError as e:
        error_handler.handle_config_validation_error(e, config)
        sys.exit(1)
    except FileNotFoundError as e:
        error_handler.handle_config_file_not_found_error(exceptions.ConfigFileNotFoundError(str(e)))
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to initialize schema: {e}")
        sys.exit(1)


@cli.group()
def backup():
    """Backup commands."""
    pass


@backup.command("incremental")
@click.option("--config", required=True, help="Path to config YAML file")
@click.option(
    "--baseline-backup",
    help="Specific backup label to use as baseline (optional). If not provided, uses the latest successful full backup.",
)
@click.option(
    "--group",
    required=True,
    help="Inventory group to backup from table_inventory. Supports wildcard '*'.",
)
@click.option(
    "--name",
    help="Optional logical name (label) for the backup. Supports -v#r placeholder for auto-versioning.",
)
def backup_incremental(config, baseline_backup, group, name):
    """Run incremental backup of partitions changed since the latest full backup.

    By default, uses the latest successful full backup as baseline.
    Optionally specify a specific backup label to use as baseline.

    Flow: load config → check health → ensure repository → reserve job slot →
    find baseline backup → find recent partitions → generate label → build backup command → execute backup
    """
    try:
        cfg = config_module.load_config(config)
        config_module.validate_config(cfg)

        with session_scope() as session:
            cluster = resolve_cluster(session, cfg, create=False)
            group_id = inventory_groups.get_group_id_by_name(session, cluster.id, group)

        def _on_progress(event: dict) -> None:
            if event.get("event") == "baseline_specified":
                logger.success(f"Using specified baseline backup: {event['baseline_backup']}")
            elif event.get("event") == "baseline_resolved":
                latest_backup = event.get("latest_backup")
                if latest_backup:
                    logger.success(
                        f"Using latest full backup as baseline: {latest_backup['label']} ({latest_backup['backup_type']})"
                    )
                else:
                    logger.warning(
                        "No full backup found - this will be the first incremental backup"
                    )

        logger.info(f"Starting incremental backup for group '{group}'...")
        result = commands.backup.run_backup_incremental(
            cluster,
            {
                "group_id": group_id,
                "repository": cfg["repository"],
                "name": name,
                "baseline_backup": baseline_backup,
            },
            on_progress=_on_progress,
        )

        logger.success(f"Backup completed successfully: {result['final_status']['state']}")
        sys.exit(0)

    except exceptions.SnapshotAlreadyExistsError as e:
        _handle_snapshot_exists_error(
            {"snapshot_name": e.snapshot_name},
            e.snapshot_name,
            config,
            cfg["repository"],
            "incremental",
            group,
            baseline_backup,
        )
        sys.exit(1)
    except exceptions.BackupExecutionError as e:
        if e.final_status.get("state") == "LOST":
            logger.critical("Backup tracking lost!")
            logger.warning("Another backup operation started during ours.")
            logger.tip("Enable run_status concurrency checks to prevent this.")
        logger.error(str(e))
        sys.exit(1)
    except exceptions.ClusterNotInitializedError as e:
        logger.error(str(e))
        sys.exit(1)
    except exceptions.ConcurrencyConflictError as e:
        error_handler.handle_concurrency_conflict_error(e, config)
        sys.exit(1)
    except exceptions.BackupLabelNotFoundError as e:
        error_handler.handle_backup_label_not_found_error(e, config)
        sys.exit(1)
    except exceptions.NoFullBackupFoundError as e:
        error_handler.handle_no_full_backup_found_error(e, config, group)
        sys.exit(1)
    except inventory_groups.InventoryGroupNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)
    except exceptions.ConfigFileNotFoundError as e:
        error_handler.handle_config_file_not_found_error(e)
        sys.exit(1)
    except exceptions.ConfigValidationError as e:
        error_handler.handle_config_validation_error(e, config)
        sys.exit(1)
    except FileNotFoundError as e:
        error_handler.handle_config_file_not_found_error(exceptions.ConfigFileNotFoundError(str(e)))
        sys.exit(1)
    except ValueError as e:
        logger.error(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


@backup.command("full")
@click.option("--config", required=True, help="Path to config YAML file")
@click.option(
    "--group",
    required=True,
    help="Inventory group to backup from table_inventory. Supports wildcard '*'.",
)
@click.option(
    "--name",
    help="Optional logical name (label) for the backup. Supports -v#r placeholder for auto-versioning.",
)
def backup_full(config, group, name):
    """Run a full backup for a specified inventory group.

    Flow: load config → check health → ensure repository → reserve job slot →
    find tables by group → generate label → build backup command → execute backup
    """
    try:
        cfg = config_module.load_config(config)
        config_module.validate_config(cfg)

        with session_scope() as session:
            cluster = resolve_cluster(session, cfg, create=False)
            group_id = inventory_groups.get_group_id_by_name(session, cluster.id, group)

        logger.info(f"Starting full backup for group '{group}'...")
        result = commands.backup.run_backup_full(
            cluster, {"group_id": group_id, "repository": cfg["repository"], "name": name}
        )

        logger.success(f"Backup completed successfully: {result['final_status']['state']}")
        sys.exit(0)

    except exceptions.SnapshotAlreadyExistsError as e:
        _handle_snapshot_exists_error(
            {"snapshot_name": e.snapshot_name}, e.snapshot_name, config, cfg["repository"], "full", group
        )
        sys.exit(1)
    except exceptions.BackupExecutionError as e:
        if e.final_status.get("state") == "LOST":
            logger.critical("Backup tracking lost!")
            logger.warning("Another backup operation started during ours.")
            logger.tip("Enable run_status concurrency checks to prevent this.")
        logger.error(str(e))
        sys.exit(1)
    except exceptions.InvalidTablesInInventoryError as e:
        error_handler.handle_invalid_tables_in_inventory_error(e, config)
        sys.exit(1)
    except exceptions.ClusterNotInitializedError as e:
        logger.error(str(e))
        sys.exit(1)
    except exceptions.ConcurrencyConflictError as e:
        error_handler.handle_concurrency_conflict_error(e, config)
        sys.exit(1)
    except inventory_groups.InventoryGroupNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)
    except exceptions.ConfigFileNotFoundError as e:
        error_handler.handle_config_file_not_found_error(e)
        sys.exit(1)
    except exceptions.ConfigValidationError as e:
        error_handler.handle_config_validation_error(e, config)
        sys.exit(1)
    except FileNotFoundError as e:
        error_handler.handle_config_file_not_found_error(exceptions.ConfigFileNotFoundError(str(e)))
        sys.exit(1)
    except ValueError as e:
        logger.error(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


@cli.command("restore")
@click.option("--config", required=True, help="Path to config YAML file")
@click.option("--target-label", required=True, help="Backup label to restore to")
@click.option("--group", help="Optional inventory group to filter tables to restore")
@click.option(
    "--table",
    help="Optional table name to restore (table name only, database comes from config). Cannot be used with --group.",
)
@click.option(
    "--rename-suffix",
    default="_restored",
    help="Suffix for temporary tables during restore (default: _restored)",
)
@click.option("--yes", is_flag=True, help="Skip confirmation prompt and proceed automatically")
def restore_command(config, target_label, group, table, rename_suffix, yes):
    """Restore data to a specific point in time using intelligent backup chain resolution.

    This command automatically determines the correct sequence of backups needed for restore:
    - For full backups: restores directly from the target backup
    - For incremental backups: restores the base full backup first, then applies the incremental

    The restore process uses temporary tables with the specified suffix for safety, then performs
    an atomic rename to make the restored data live.

    Flow: load config → check health → ensure repository → find restore pair → get tables from backup → execute restore flow
    """
    try:
        if group and table:
            logger.error(
                "Cannot specify both --group and --table. Use --table for single table restore or --group for inventory group restore."
            )
            sys.exit(1)

        if table:
            table = table.strip()
            if not table:
                raise exceptions.InvalidTableNameError("", "Table name cannot be empty")

            if "." in table:
                raise exceptions.InvalidTableNameError(
                    table,
                    "Table name must not include database prefix. Use 'table_name' not 'database.table_name'",
                )

        cfg = config_module.load_config(config)
        config_module.validate_config(cfg)

        with session_scope() as session:
            cluster = resolve_cluster(session, cfg, create=False)
            group_id = inventory_groups.get_group_id_by_name(session, cluster.id, group) if group else None

        logger.info(f"Finding restore sequence for target backup: {target_label}")
        result = commands.restore.run_restore(
            cluster,
            {
                "target_label": target_label,
                "group_id": group_id,
                "table": table,
                "database": cfg["database"] if table else None,
                "rename_suffix": rename_suffix,
            },
            skip_confirmation=yes,
        )

        logger.success(result["message"])
        sys.exit(0)

    except exceptions.RestoreExecutionError as e:
        logger.error(f"Restore failed: {e}")
        sys.exit(1)
    except exceptions.InvalidTableNameError as e:
        error_handler.handle_invalid_table_name_error(e)
        sys.exit(1)
    except exceptions.ClusterNotInitializedError as e:
        logger.error(str(e))
        sys.exit(1)
    except exceptions.BackupLabelNotFoundError as e:
        error_handler.handle_backup_label_not_found_error(e, config)
        sys.exit(1)
    except exceptions.NoSuccessfulFullBackupFoundError as e:
        error_handler.handle_no_successful_full_backup_found_error(e, config)
        sys.exit(1)
    except exceptions.TableNotFoundInBackupError as e:
        error_handler.handle_table_not_found_in_backup_error(e, config)
        sys.exit(1)
    except exceptions.NoTablesFoundError as e:
        error_handler.handle_no_tables_found_error(e, config, target_label)
        sys.exit(1)
    except inventory_groups.InventoryGroupNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)
    except exceptions.SnapshotNotFoundError as e:
        error_handler.handle_snapshot_not_found_error(e, config)
        sys.exit(1)
    except exceptions.RestoreOperationCancelledError:
        error_handler.handle_restore_operation_cancelled_error()
        sys.exit(1)
    except exceptions.ConfigFileNotFoundError as e:
        error_handler.handle_config_file_not_found_error(e)
        sys.exit(1)
    except exceptions.ConfigValidationError as e:
        error_handler.handle_config_validation_error(e, config)
        sys.exit(1)
    except exceptions.ClusterHealthCheckFailedError as e:
        error_handler.handle_cluster_health_check_failed_error(e, config)
        sys.exit(1)
    except FileNotFoundError as e:
        error_handler.handle_config_file_not_found_error(exceptions.ConfigFileNotFoundError(str(e)))
        sys.exit(1)
    except ValueError as e:
        error_handler.handle_config_validation_error(
            exceptions.ConfigValidationError(str(e)), config
        )
        sys.exit(1)
    except exceptions.ConcurrencyConflictError as e:
        error_handler.handle_concurrency_conflict_error(e, config)
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


@cli.command("prune")
@click.option("--config", required=True, help="Path to config YAML file")
@click.option(
    "--group",
    required=True,
    help="Inventory group whose backups to prune. Pruning is always scoped to one group.",
)
@click.option(
    "--keep-last",
    type=int,
    help="Keep only the last N successful backups (deletes older ones)",
)
@click.option(
    "--older-than",
    help="Delete snapshots older than this timestamp (format: YYYY-MM-DD HH:MM:SS)",
)
@click.option("--snapshot", help="Delete a specific snapshot by name")
@click.option("--snapshots", help="Delete multiple specific snapshots (comma-separated)")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be deleted without actually deleting",
)
@click.option("--yes", is_flag=True, help="Skip confirmation prompt and proceed automatically")
def prune_command(config, group, keep_last, older_than, snapshot, snapshots, dry_run, yes):
    """Prune (delete) old backup snapshots from the repository.

    This command helps manage repository storage by removing old or unwanted snapshots.
    Supports multiple pruning strategies:
    - Keep only the last N backups
    - Delete backups older than a specific date
    - Delete specific snapshots by name

    Flow: load config → check health → ensure repository → query backups →
    filter snapshots to delete → confirm → execute deletion → cleanup history
    """
    try:
        pruning_options = [keep_last, older_than, snapshot, snapshots]
        specified_options = [opt for opt in pruning_options if opt is not None]

        if not specified_options:
            logger.error(
                "Must specify one pruning option: --keep-last, --older-than, --snapshot, or --snapshots"
            )
            sys.exit(1)

        if len(specified_options) > 1:
            logger.error(
                "Pruning options are mutually exclusive. "
                "Please specify only one of: --keep-last, --older-than, --snapshot, or --snapshots"
            )
            sys.exit(1)

        if keep_last is not None and keep_last <= 0:
            logger.error("--keep-last must be a positive number (greater than 0)")
            sys.exit(1)

        cfg = config_module.load_config(config)
        config_module.validate_config(cfg)

        if keep_last:
            logger.info(f"Pruning strategy: Keep last {keep_last} backup(s)")
        elif older_than:
            logger.info(f"Pruning strategy: Delete backups older than {older_than}")
        elif snapshot:
            logger.info(f"Pruning strategy: Delete specific snapshot '{snapshot}'")
        elif snapshots:
            logger.info(
                f"Pruning strategy: Delete {len(snapshots.split(','))} specific snapshot(s)"
            )

        if group:
            logger.info(f"Filtering by inventory group: {group}")

        with session_scope() as session:
            cluster = resolve_cluster(session, cfg, create=False)
            group_id = inventory_groups.get_group_id_by_name(session, cluster.id, group) if group else None

        params = {
            "group_id": group_id,
            "keep_last": keep_last,
            "older_than": older_than,
            "snapshot": snapshot,
            "snapshots": snapshots,
        }

        # Plan first (dry_run=True is side-effect-free) so we know what would be
        # deleted before honoring --dry-run or prompting for confirmation; the
        # actual deletion below re-runs the same planning step for real.
        plan = commands.prune.run_prune(cluster, {**params, "dry_run": True})

        if "would_delete" not in plan:
            logger.info(f"No successful backups found for inventory group '{group}'")
            sys.exit(0)

        snapshots_to_delete = plan["would_delete"]

        if not snapshots_to_delete:
            logger.success("No snapshots to delete based on the specified criteria")
            sys.exit(0)

        logger.info("")
        logger.info(f"Snapshots to delete: {len(snapshots_to_delete)}")
        for label in snapshots_to_delete:
            logger.info(f"  - {label}")

        if keep_last:
            logger.info(f"Snapshots to keep: {plan['kept_count']} (most recent)")

        if dry_run:
            logger.info("")
            logger.warning("DRY RUN MODE - No snapshots will be deleted")
            logger.info(f"Would delete {len(snapshots_to_delete)} snapshot(s)")
            sys.exit(0)

        if not yes:
            logger.info("")
            logger.warning(
                f"This will permanently delete {len(snapshots_to_delete)} snapshot(s) from the repository"
            )
            confirm = click.confirm("Do you want to proceed?", default=False)
            if not confirm:
                logger.info("Prune operation cancelled by user")
                sys.exit(1)

        logger.info("")
        logger.info("Starting snapshot deletion...")
        result = commands.prune.run_prune(cluster, {**params, "dry_run": False})

        logger.info("")
        logger.success(f"Deleted {len(result['deleted'])} snapshot(s)")

        if keep_last:
            logger.success(f"Kept {result['kept_count']} most recent backup(s)")

        sys.exit(0)

    except exceptions.ClusterNotInitializedError as e:
        logger.error(str(e))
        sys.exit(1)
    except inventory_groups.InventoryGroupNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)
    except exceptions.ConfigFileNotFoundError as e:
        error_handler.handle_config_file_not_found_error(e)
        sys.exit(1)
    except exceptions.ConfigValidationError as e:
        error_handler.handle_config_validation_error(e, config)
        sys.exit(1)
    except FileNotFoundError as e:
        error_handler.handle_config_file_not_found_error(exceptions.ConfigFileNotFoundError(str(e)))
        sys.exit(1)
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


if api_group is not None:
    cli.add_command(api_group)


if __name__ == "__main__":
    cli()
