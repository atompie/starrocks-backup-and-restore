"""The single implementation of job submission.

Shared by direct API job-submission routes and `commands.schedules.run_due_schedules`,
per specs/api-scheduling "submits a job through the same job-submission path
used by direct API job submission". Raises `jobs.backend.UnknownBackendError`
(already a plain `ValueError` subclass, no HTTP concept) instead of
`HTTPException` on an unresolvable backend - the caller translates it.
"""

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..jobs.backend import get_registry
from ..store.models import Cluster, Job


def submit_job(
    db: Session,
    cluster: Cluster,
    job_type: str,
    params: dict,
    requested_backend: str | None,
) -> Job:
    """Create a Job row and enqueue it on the resolved backend."""
    registry = get_registry()
    backend_name = registry.resolve(requested_backend, cluster.default_backend)

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
    # Commit now (not just flush): the enqueued backend may run the job in a
    # separate thread/connection immediately, which must see this row.
    db.commit()

    registry.get(backend_name).enqueue(job.id)
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
