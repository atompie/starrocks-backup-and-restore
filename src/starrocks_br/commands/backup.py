"""The single implementation of the backup_full/backup_incremental use cases.

Runs the core-library calls needed to execute a backup (see
openspec/changes/establish-command-layer). Takes no dependency on HTTP
frameworks; the API's job backend calls these functions directly.
"""

import datetime
from collections.abc import Callable
from typing import Any

from .. import concurrency, executor, logger, planner
from ..dal.metadata import history
from ..dal.metadata import jobs as jobs_dal
from ..dal.metadata import labels
from ..exceptions import BackupExecutionError, SnapshotAlreadyExistsError
from ..store.models import Cluster
from ..store.session import get_session_factory, session_scope
from ._shared import connect, ensure_ready

OnProgress = Callable[[dict], None] | None


def _set_job_label(job_id: int, label: str) -> None:
    """Persist the backup label onto the job's row once determined.

    `restore.find_restore_pair`/`find_backup_repository` resolve restore lineage from
    `Job.label` (see design.md) rather than a separate backup catalog; the label isn't
    known until `labels.determine_backup_label` computes it, so it's written here rather
    than at job submission time.

    For a job spanning more than one database (backup-references/design.md Decision 4), this
    holds only the first database processed - `Job.label` is a single column and can't carry a
    label per database; the full set of a job's labels lives in its `backup_references` rows.
    """
    with session_scope() as session:
        jobs_dal.set_label(session, job_id, label)


def _set_job_baseline(job_id: int, baseline_job_id: int | None) -> None:
    """Persist an incremental job's baseline full-job id once resolved.

    Mirrors `_set_job_label` exactly: `planner.find_recent_partitions` only resolves the
    baseline (either from an explicit label or the latest full backup) after this job's row
    already exists, so it's written here rather than at job submission time. For a multi-database
    incremental job, this holds the first database's resolved baseline, for the same reason
    `_set_job_label` only holds the first database's label.
    """
    with session_scope() as session:
        jobs_dal.set_baseline_job_id(session, job_id, baseline_job_id)


def _append_failure_history(job_id: int, exc: Exception) -> None:
    """Best-effort append of a `FAILED` history row for a failure outside `execute_backup`'s own
    result-dict handling (e.g. building the backup command, resolving partitions, recording
    references). `execute_backup` already records its own submit/poll failures; this covers the
    rest of the per-database loop, so a job that ends `FAILED` always has a history row saying why.
    """
    try:
        history.append_backup_event(get_session_factory(), job_id, "FAILED", message=str(exc))
    except Exception:
        logger.error(f"Failed to append backup history for job {job_id}")


def _raise_for_backup_failure(result: dict) -> None:
    """Translate `executor.execute_backup`'s failure dict into a domain exception.

    `execute_backup` itself keeps returning `{"success": False, ...}` (see
    design.md Decision 2) - this is the one place that dict is translated
    into the exception types the API's job backend expects.
    """
    error_details = result.get("error_details") or {}
    if error_details.get("error_type") == "snapshot_exists":
        raise SnapshotAlreadyExistsError(error_details["snapshot_name"])
    raise BackupExecutionError(result["error_message"], final_status=result.get("final_status"))


def run_backup_full(
    cluster: Cluster, params: dict[str, Any], job_id: int, on_progress: OnProgress = None
) -> dict:
    group = params.get("group_id")
    if not group:
        raise ValueError("'group_id' is required for backup_full")
    repository = params.get("repository")
    if not repository:
        raise ValueError("'repository' is required for backup_full")
    name = params.get("name")

    database = connect(cluster)
    with database:
        ensure_ready(database, cluster, repository=repository)

        with session_scope() as session:
            group_databases = planner.resolve_group_databases(session, cluster.id, group)
            tables = planner.find_tables_by_group(session, cluster.id, group)
            primary_label = labels.determine_backup_label(
                session, cluster.id, "full", group_databases[0], custom_name=name
            )
            _set_job_label(job_id, primary_label)
            concurrency.reserve_job_slot(database, session, cluster.id, "backup", primary_label)

        final_status = None
        final_state = "FINISHED"
        try:
            try:
                for index, group_database in enumerate(group_databases):
                    with session_scope() as session:
                        if index == 0:
                            label = primary_label
                        else:
                            custom_name = f"{name}_{group_database}" if name else None
                            label = labels.determine_backup_label(
                                session, cluster.id, "full", group_database, custom_name=custom_name
                            )

                        planner.validate_tables_exist(database, group_database, tables, group)

                        backup_command = planner.build_full_backup_command(
                            session, cluster.id, group, repository, label, group_database
                        )
                        if not backup_command:
                            raise RuntimeError(
                                f"No tables found in group '{group}' for database '{group_database}' to backup"
                            )

                        all_partitions = planner.get_all_partitions_for_tables(database, group_database, tables)

                    result = executor.execute_backup(
                        database,
                        cluster.id,
                        backup_command,
                        repository=repository,
                        backup_type="full",
                        scope="backup",
                        database=group_database,
                        job_id=job_id,
                        on_progress=on_progress,
                        release_slot=False,
                    )

                    if not result["success"]:
                        raise _BackupLoopFailure(result)

                    final_status = result["final_status"]
                    with session_scope() as session:
                        planner.record_backup_references(
                            session,
                            job_id,
                            repository,
                            label,
                            datetime.datetime.now(datetime.timezone.utc),
                            all_partitions,
                        )
            except _BackupLoopFailure as failure:
                final_state = (failure.result.get("final_status") or {}).get("state") or "FAILED"
                _raise_for_backup_failure(failure.result)
            except Exception as exc:
                final_state = "FAILED"
                _append_failure_history(job_id, exc)
                raise
        finally:
            with session_scope() as session:
                concurrency.complete_job_slot(
                    session, cluster.id, scope="backup", label=primary_label, final_state=final_state
                )

        return {"label": primary_label, "final_status": final_status}


def run_backup_incremental(
    cluster: Cluster, params: dict[str, Any], job_id: int, on_progress: OnProgress = None
) -> dict:
    group = params.get("group_id")
    if not group:
        raise ValueError("'group_id' is required for backup_incremental")
    repository = params.get("repository")
    if not repository:
        raise ValueError("'repository' is required for backup_incremental")
    name = params.get("name")
    baseline_backup = params.get("baseline_backup")

    database = connect(cluster)
    with database:
        ensure_ready(database, cluster, repository=repository)

        with session_scope() as session:
            group_databases = planner.resolve_group_databases(session, cluster.id, group)
            primary_label = labels.determine_backup_label(
                session, cluster.id, "incremental", group_databases[0], custom_name=name
            )
            _set_job_label(job_id, primary_label)
            concurrency.reserve_job_slot(database, session, cluster.id, "backup", primary_label)

        final_status = None
        final_state = "FINISHED"
        primary_baseline_job_id = None
        try:
            try:
                for index, group_database in enumerate(group_databases):
                    with session_scope() as session:
                        if index == 0:
                            label = primary_label
                        else:
                            custom_name = f"{name}_{group_database}" if name else None
                            label = labels.determine_backup_label(
                                session, cluster.id, "incremental", group_database, custom_name=custom_name
                            )

                        if baseline_backup:
                            if on_progress:
                                on_progress(
                                    {"event": "baseline_specified", "baseline_backup": baseline_backup}
                                )
                        else:
                            latest_backup = planner.find_latest_full_backup(
                                database, session, cluster.id, group_database
                            )
                            if on_progress:
                                on_progress({"event": "baseline_resolved", "latest_backup": latest_backup})

                        partitions, baseline_job_id = planner.find_recent_partitions(
                            database,
                            session,
                            cluster.id,
                            group_database,
                            baseline_backup_label=baseline_backup,
                            group_id=group,
                        )
                        if index == 0:
                            primary_baseline_job_id = baseline_job_id
                        if not partitions:
                            raise RuntimeError(f"No partitions found to backup for database '{group_database}'")

                        backup_command = planner.build_incremental_backup_command(
                            partitions, repository, label, group_database
                        )

                    result = executor.execute_backup(
                        database,
                        cluster.id,
                        backup_command,
                        repository=repository,
                        backup_type="incremental",
                        scope="backup",
                        database=group_database,
                        job_id=job_id,
                        on_progress=on_progress,
                        release_slot=False,
                    )

                    if not result["success"]:
                        raise _BackupLoopFailure(result)

                    final_status = result["final_status"]
                    with session_scope() as session:
                        planner.record_backup_references(
                            session,
                            job_id,
                            repository,
                            label,
                            datetime.datetime.now(datetime.timezone.utc),
                            partitions,
                        )

                _set_job_baseline(job_id, primary_baseline_job_id)
            except _BackupLoopFailure as failure:
                final_state = (failure.result.get("final_status") or {}).get("state") or "FAILED"
                _raise_for_backup_failure(failure.result)
            except Exception as exc:
                final_state = "FAILED"
                _append_failure_history(job_id, exc)
                raise
        finally:
            with session_scope() as session:
                concurrency.complete_job_slot(
                    session, cluster.id, scope="backup", label=primary_label, final_state=final_state
                )

        return {"label": primary_label, "final_status": final_status}


class _BackupLoopFailure(Exception):
    """Internal signal that one database's `execute_backup` call failed mid-loop.

    Carries the failed call's result dict so the `except` block can record the concurrency
    slot's final state before re-raising as the domain exception `_raise_for_backup_failure`
    produces; the slot itself is released by the enclosing `finally`, once, for every exit path.
    """

    def __init__(self, result: dict) -> None:
        self.result = result
        super().__init__(result.get("error_message"))
