"""The single implementation of job submission.

Shared by direct API job-submission routes and `commands.schedules.run_due_schedules`,
per specs/api-scheduling "submits a job through the same job-submission path
used by direct API job submission". Raises `jobs.backend.UnknownBackendError`
(already a plain `ValueError` subclass, no HTTP concept) instead of
`HTTPException` on an unresolvable backend - the caller translates it.
"""

from sqlalchemy.orm import Session

from ..dal.metadata import jobs as jobs_dal
from ..jobs.backend import get_registry
from ..store.models import Cluster, Job


def submit_job(
    db: Session,
    cluster: Cluster,
    job_type: str,
    params: dict,
    requested_backend: str | None,
    schedule_id: int | None = None,
    source_backup_job_id: int | None = None,
) -> Job:
    """Create a Job row and enqueue it on the resolved backend."""
    registry = get_registry()
    backend_name = registry.resolve(requested_backend, cluster.default_backend)

    job = jobs_dal.create_job(
        db,
        cluster,
        job_type,
        params,
        backend_name,
        schedule_id=schedule_id,
        source_backup_job_id=source_backup_job_id,
    )

    registry.get(backend_name).enqueue(job.id)
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
    return jobs_dal.list_jobs(
        db,
        cluster_id,
        job_type=job_type,
        status=status,
        job_id=job_id,
        group_id=group_id,
        schedule_id=schedule_id,
        limit=limit,
        offset=offset,
    )


def get_job(db: Session, job_id: int) -> Job | None:
    return jobs_dal.get(db, job_id)


def get_job_history(db: Session, job_type: str, job_id: int) -> list:
    return jobs_dal.list_history_for_job(db, job_type, job_id)


def get_job_references(db: Session, job_id: int) -> list:
    return jobs_dal.list_references_for_job(db, job_id)
