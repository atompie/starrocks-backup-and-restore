import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...store.models import Cluster, Job


def create_job(
    db: Session,
    cluster: Cluster,
    job_type: str,
    params: dict,
    backend_name: str,
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
    limit: int = 50,
    offset: int = 0,
) -> list[Job]:
    """List jobs for a cluster, most recently created first.

    `job_type` may be a single type or a list of types (e.g. the default
    backup-history scope of `backup_full`/`backup_incremental`). `group_id`
    only matches jobs whose params carried a group id; jobs with no
    recorded group id (including any submitted before that column existed)
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

    query = query.order_by(Job.created_at.desc()).limit(limit).offset(offset)

    return list(db.scalars(query).all())
