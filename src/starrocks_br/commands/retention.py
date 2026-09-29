"""The single implementation of schedule-scoped retention (SPEC.md §10, §11, §19, §21, §23).

`submit_due_retention_jobs` is the tick's sweep: it queues a `retention` job for every recurring
full-backup schedule that has droppable backups. `run_retention` is that job's handler: it drops
the schedule's droppable backups one at a time. It takes no concurrency slot of its own - the
dispatcher runs one job per cluster - and holds no metadata session while StarRocks runs.
"""

import datetime
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from .. import logger
from ..dal.db import prune as prune_db
from ..dal.metadata import clusters as clusters_dal
from ..dal.metadata import history
from ..dal.metadata import jobs as jobs_dal
from ..dal.metadata import retention as retention_dal
from ..runtime_config import get_retention_max_seconds
from ..store.models import Cluster, JobType, Schedule
from ..store.session import get_session_factory, session_scope
from ._shared import connect, ensure_ready
from .jobs import submit_job

OnProgress = Callable[[dict], None] | None


def submit_due_retention_jobs(session: Session) -> list[int]:
    """Queue a `retention` job for each schedule with droppable backups and no open retention job.

    Only droppable backups count, so old backups that are merely protected never make a job
    every tick. Returns the ids of the jobs submitted.
    """
    submitted: list[int] = []
    for schedule in retention_dal.schedules_subject_to_retention(session):
        if not retention_dal.droppable_backups(session, schedule):
            continue
        if jobs_dal.schedule_has_open_job(session, schedule.id, JobType.RETENTION.value):
            continue

        cluster = clusters_dal.get(session, schedule.cluster_id)
        job = submit_job(
            session,
            cluster,
            JobType.RETENTION.value,
            {"schedule_id": schedule.id},
            schedule.backend,
            schedule_id=schedule.id,
        )
        submitted.append(job.id)
        logger.info(f"Queued retention job {job.id} for schedule {schedule.id}")
    return submitted


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _droppable_snapshots(schedule_id: int, backup_id: int) -> list[tuple[str, str]] | None:
    """The backup's live snapshots if it is still droppable right now, else `None`.

    Re-evaluated immediately before each drop: a restore or incremental may have been queued
    against the backup since the job started.
    """
    with session_scope() as session:
        schedule = session.get(Schedule, schedule_id)
        if schedule is None or schedule.deletion_requested_at is not None:
            return None
        if backup_id not in {job.id for job in retention_dal.droppable_backups(session, schedule)}:
            return None
        return retention_dal.live_snapshots_for_job(session, backup_id)


def run_retention(
    cluster: Cluster, params: dict[str, Any], job_id: int, on_progress: OnProgress = None
) -> dict:
    """The `retention` job: drop a schedule's droppable full backups, one backup at a time.

    Each backup's snapshots are dropped first (one already gone counts as dropped), then its
    references are marked deleted in a short session, so metadata always matches what was
    physically dropped. New drops stop once `STARROCKS_BR_RETENTION_MAX_SECONDS` has elapsed; that
    ends normally and a later tick queues another job for the rest. A failed drop stops the run
    and fails the job, leaving that backup for a later job. No backup job's status is touched.
    """
    del on_progress  # progress is recorded in retention_history

    schedule_id = params["schedule_id"]
    factory = get_session_factory()
    deadline = time.monotonic() + get_retention_max_seconds()
    dropped: list[int] = []
    skipped: list[int] = []
    deadline_reached = False

    history.append_retention_event(factory, job_id, "RETENTION_STARTED", details={"schedule_id": schedule_id})

    try:
        with session_scope() as session:
            schedule = session.get(Schedule, schedule_id)
            candidate_ids = (
                [job.id for job in retention_dal.droppable_backups(session, schedule)]
                if schedule is not None and schedule.deletion_requested_at is None
                else []
            )

        if candidate_ids:
            database = connect(cluster)
            with database:
                ensure_ready(database, cluster)
                for backup_id in candidate_ids:
                    if time.monotonic() >= deadline:
                        deadline_reached = True
                        break

                    snapshots = _droppable_snapshots(schedule_id, backup_id)
                    if snapshots is None:
                        skipped.append(backup_id)
                        continue

                    for repository, snapshot_label in snapshots:
                        try:
                            if prune_db.snapshot_present(database, repository, snapshot_label):
                                prune_db.execute_drop_snapshot(database, repository, snapshot_label)
                        except Exception as e:
                            history.append_retention_event(
                                factory,
                                job_id,
                                "ERROR",
                                message=str(e),
                                details={"backup_job_id": backup_id, "repository": repository, "snapshot": snapshot_label},
                            )
                            raise

                    with session_scope() as session:
                        retention_dal.mark_references_deleted(session, backup_id, _utcnow())
                    dropped.append(backup_id)
                    history.append_retention_event(
                        factory,
                        job_id,
                        "SNAPSHOT_DROPPED",
                        details={"backup_job_id": backup_id, "snapshots": [label for _, label in snapshots]},
                    )
    except Exception as e:
        history.append_retention_event(factory, job_id, "FAILED", message=str(e), details={"dropped": dropped})
        raise

    result = {
        "schedule_id": schedule_id,
        "dropped": dropped,
        "skipped_protected": skipped,
        "deadline_reached": deadline_reached,
    }
    history.append_retention_event(factory, job_id, "RETENTION_FINISHED", details=result)
    return result
