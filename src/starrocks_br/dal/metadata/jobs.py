import datetime
import json

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session

from ...store.models import (
    BackupHistory,
    BackupReference,
    Cluster,
    Job,
    JobStatus,
    RestoreHistory,
    RetentionHistory,
)


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def create_job(
    db: Session,
    cluster: Cluster,
    job_type: str,
    params: dict,
    backend_name: str,
    schedule_id: int | None = None,
    source_backup_job_id: int | None = None,
) -> Job:
    """Insert and commit a new Job row.

    Commits now (not just flushes): the enqueued backend may run the job in a
    separate thread/connection immediately, which must see this row.
    """
    job = Job(
        cluster_id=cluster.id,
        job_type=job_type,
        group_id=params.get("group_id"),
        params_json=json.dumps(params),
        backend=backend_name,
        repository=params.get("repository"),
        schedule_id=schedule_id,
        source_backup_job_id=source_backup_job_id,
    )
    db.add(job)
    db.flush()
    db.refresh(job)
    db.commit()
    return job


def list_jobs(
    db: Session,
    cluster_id: int,
    job_type: str | list[str] | None = None,
    status: str | None = None,
    job_id: int | None = None,
    group_id: int | None = None,
    schedule_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Job]:
    """List jobs for a cluster, most recently created first.

    `job_type` may be a single type or a list of types (e.g. the default
    backup-history scope of `backup_full`/`backup_incremental`). `group_id`
    only matches jobs whose params carried a group id; jobs with no
    recorded group id (including any submitted before that column existed)
    never match it. `schedule_id` only matches jobs submitted by that
    schedule; jobs submitted directly (or before schedule linkage existed)
    never match it.
    """
    query = select(Job).where(Job.cluster_id == cluster_id)

    if job_type is not None:
        if isinstance(job_type, list):
            query = query.where(Job.job_type.in_(job_type))
        else:
            query = query.where(Job.job_type == job_type)

    if status is not None:
        query = query.where(Job.status == status)

    if job_id is not None:
        query = query.where(Job.id == job_id)

    if group_id is not None:
        query = query.where(Job.group_id == group_id)

    if schedule_id is not None:
        query = query.where(Job.schedule_id == schedule_id)

    query = query.order_by(Job.created_at.desc()).limit(limit).offset(offset)

    return list(db.scalars(query).all())


def get(db: Session, job_id: int) -> Job | None:
    return db.get(Job, job_id)


def set_label(db: Session, job_id: int, label: str) -> None:
    job = db.get(Job, job_id)
    if job is not None:
        job.label = label


def set_baseline_job_id(db: Session, job_id: int, baseline_job_id: int | None) -> None:
    job = db.get(Job, job_id)
    if job is not None:
        job.baseline_job_id = baseline_job_id


def mark_running(db: Session, job_id: int) -> Job | None:
    job = db.get(Job, job_id)
    if job is not None:
        now = _utcnow()
        job.status = JobStatus.RUNNING.value
        job.started_at = now
        job.heartbeat_at = now
        db.flush()
    return job


# Admission priority within one cluster's lane: restore first, then backups, then everything else
# (schedule cleanup, retention, ...).
_ADMISSION_PRIORITY = case(
    (Job.job_type == "restore", 0),
    (Job.job_type.in_(["backup_full", "backup_incremental"]), 1),
    else_=2,
)


def clusters_with_pending_jobs(db: Session) -> list[int]:
    """Ids of clusters that have at least one `PENDING` job, lowest id first."""
    return list(
        db.scalars(
            select(Job.cluster_id)
            .where(Job.status == JobStatus.PENDING.value)
            .distinct()
            .order_by(Job.cluster_id)
        ).all()
    )


def cluster_has_running_job(db: Session, cluster_id: int) -> bool:
    return (
        db.scalar(
            select(Job.id)
            .where(Job.cluster_id == cluster_id, Job.status == JobStatus.RUNNING.value)
            .limit(1)
        )
        is not None
    )


def next_pending_job(db: Session, cluster_id: int) -> Job | None:
    """The `PENDING` job to admit next on a cluster: highest priority, then oldest (lowest id)."""
    return db.scalars(
        select(Job)
        .where(Job.cluster_id == cluster_id, Job.status == JobStatus.PENDING.value)
        .order_by(_ADMISSION_PRIORITY, Job.id)
        .limit(1)
    ).first()


def schedule_has_open_job(db: Session, schedule_id: int, job_type: str) -> bool:
    """Whether the schedule already has a `PENDING` or `RUNNING` job of `job_type`.

    Scoped to the schedule's own job type so other work tied to the schedule (retention,
    cleanup) never blocks its next backup.
    """
    return (
        db.scalar(
            select(Job.id)
            .where(
                Job.schedule_id == schedule_id,
                Job.job_type == job_type,
                Job.status.in_([JobStatus.PENDING.value, JobStatus.RUNNING.value]),
            )
            .limit(1)
        )
        is not None
    )


def claim_pending_job(db: Session, job_id: int, now: datetime.datetime | None = None) -> bool:
    """Atomically admit a `PENDING` job: `PENDING` -> `RUNNING`, stamping `started_at`/`heartbeat_at`.

    Returns `True` only for the caller whose conditional update matched the row, so two
    dispatchers considering the same job cannot both enqueue it.
    """
    now = now or _utcnow()
    result = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == JobStatus.PENDING.value)
        .values(status=JobStatus.RUNNING.value, started_at=now, heartbeat_at=now)
    )
    db.flush()
    return result.rowcount == 1


def touch_heartbeat(db: Session, job_id: int, now: datetime.datetime | None = None) -> None:
    """Record that `job_id`'s owner is still alive. Only touches a job that is still `RUNNING`."""
    db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == JobStatus.RUNNING.value)
        .values(heartbeat_at=now or _utcnow())
    )


# A `RUNNING` job's liveness: its last heartbeat, else when it started, else when it was created.
_liveness = func.coalesce(Job.heartbeat_at, Job.started_at, Job.created_at)


def list_stale_jobs(db: Session, cutoff: datetime.datetime) -> list[Job]:
    """`RUNNING` jobs whose liveness is older than `cutoff`, oldest first.

    A `PENDING` job is waiting for its cluster lane to free up, so its age is queue time, not
    staleness: it is never returned here.
    """
    return list(
        db.scalars(
            select(Job)
            .where(Job.status == JobStatus.RUNNING.value, _liveness < cutoff)
            .order_by(Job.id)
        ).all()
    )


def claim_stale_job(
    db: Session, job_id: int, expected_status: str, cutoff: datetime.datetime, now: datetime.datetime
) -> bool:
    """Atomically claim a job that is still stale and still in `expected_status`.

    Claiming stamps `heartbeat_at = now`, which makes the job fresh to every other claimant, so
    exactly one of several concurrent callers gets `True`.
    """
    result = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == expected_status, _liveness < cutoff)
        .values(heartbeat_at=now)
    )
    db.flush()
    return result.rowcount == 1


def mark_progress(db: Session, job_id: int, state_detail: str | None, progress_pct: int | None) -> None:
    job = db.get(Job, job_id)
    if job is None:
        return
    job.state_detail = state_detail
    if progress_pct is not None:
        job.progress_pct = progress_pct


def mark_failed(db: Session, job_id: int, error_message: str) -> None:
    job = db.get(Job, job_id)
    if job is not None:
        job.status = JobStatus.FAILED.value
        job.error_message = error_message
        job.finished_at = _utcnow()


def mark_success(db: Session, job_id: int, result_json: str) -> None:
    job = db.get(Job, job_id)
    if job is not None:
        job.status = JobStatus.SUCCESS.value
        job.result_json = result_json
        job.finished_at = _utcnow()


_HISTORY_MODEL_BY_JOB_TYPE = {
    "backup_full": BackupHistory,
    "backup_incremental": BackupHistory,
    "restore": RestoreHistory,
    "retention": RetentionHistory,
}


def list_history_for_job(db: Session, job_type: str, job_id: int) -> list[BackupHistory | RestoreHistory | RetentionHistory]:
    """Return a job's append-only execution history, oldest first.

    A job type with no history table (e.g. `prune`) returns an empty list.
    """
    model = _HISTORY_MODEL_BY_JOB_TYPE.get(job_type)
    if model is None:
        return []
    return list(db.scalars(select(model).where(model.job_id == job_id).order_by(model.ts.asc())).all())


def list_references_for_job(db: Session, job_id: int) -> list[BackupReference]:
    """Return a backup job's recorded references.

    Only a job whose StarRocks operation reached `FINISHED` has any rows (SPEC.md §16) - a
    `RUNNING` or `FAILED` job simply returns an empty list, same as `list_history_for_job` does
    for a job type with no log table.
    """
    return list(
        db.scalars(
            select(BackupReference).where(BackupReference.job_id == job_id).order_by(BackupReference.id.asc())
        ).all()
    )
