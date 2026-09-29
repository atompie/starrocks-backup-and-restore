import datetime
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...store.models import BackupHistory, BackupReference, Cluster, Job, JobStatus, RestoreHistory


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
        job.status = JobStatus.RUNNING.value
        job.started_at = _utcnow()
        db.flush()
    return job


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
}


def list_history_for_job(db: Session, job_type: str, job_id: int) -> list[BackupHistory | RestoreHistory]:
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
