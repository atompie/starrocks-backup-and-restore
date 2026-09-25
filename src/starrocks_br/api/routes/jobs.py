import json

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ... import inventory_groups
from ...jobs.backend import UnknownBackendError, get_registry
from ...store.models import Cluster, Job
from ..auth import require_api_key
from ..deps import get_db
from ..schemas import BackupFullRequest, BackupIncrementalRequest, JobRead, PruneRequest, RestoreRequest
from ._cluster_connect import connect_or_503, get_cluster_or_404 as _get_cluster_or_404

router = APIRouter(tags=["jobs"], dependencies=[Depends(require_api_key)])


def submit_job(
    db: Session,
    cluster: Cluster,
    job_type: str,
    params: dict,
    requested_backend: str | None,
) -> Job:
    """Create a Job row and enqueue it on the resolved backend.

    Shared by direct job-submission routes and the schedules run-due route,
    per specs/api-scheduling "submits a job through the same job-submission
    path used by direct API job submission".
    """
    registry = get_registry()
    try:
        backend_name = registry.resolve(requested_backend, cluster.default_backend)
    except UnknownBackendError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e

    job = Job(
        cluster_id=cluster.id,
        job_type=job_type,
        params_json=json.dumps(params),
        backend=backend_name,
    )
    db.add(job)
    db.flush()
    db.refresh(job)
    # Commit now (not just flush): the enqueued backend may run the job in a
    # separate thread/connection immediately, which must see this row.
    db.commit()

    registry.get(backend_name).enqueue(job.id)
    return job


def _submit(
    db: Session, cluster_id: int, job_type: str, payload: BaseModel
) -> Job:
    cluster = _get_cluster_or_404(db, cluster_id)
    params = payload.model_dump(exclude={"backend"})
    return submit_job(db, cluster, job_type, params, payload.backend)


def _submit_backup_job(
    db: Session,
    cluster_id: int,
    job_type: str,
    payload: BackupFullRequest | BackupIncrementalRequest,
) -> Job:
    """Submit a backup_full/backup_incremental job, failing fast on an unknown group.

    Per specs/api-job-execution "Submitting an operation returns immediately
    with a job", a missing group is rejected with 422 by Pydantic before this
    function runs; an unknown group is rejected synchronously with 404 before
    a job is ever created, instead of letting the job fail later asynchronously.
    """
    cluster = _get_cluster_or_404(db, cluster_id)

    database = connect_or_503(cluster)
    try:
        if not inventory_groups.group_exists(database, payload.group, cluster.ops_database):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Inventory group '{payload.group}' not found on cluster '{cluster.name}'",
            )
    finally:
        database.close()

    params = payload.model_dump(exclude={"backend"})
    return submit_job(db, cluster, job_type, params, payload.backend)


@router.post(
    "/clusters/{cluster_id}/backups/full",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_backup_full(
    cluster_id: int, payload: BackupFullRequest, db: Session = Depends(get_db)
) -> Job:
    return _submit_backup_job(db, cluster_id, "backup_full", payload)


@router.post(
    "/clusters/{cluster_id}/backups/incremental",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_backup_incremental(
    cluster_id: int, payload: BackupIncrementalRequest, db: Session = Depends(get_db)
) -> Job:
    return _submit_backup_job(db, cluster_id, "backup_incremental", payload)


@router.post(
    "/clusters/{cluster_id}/restores",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_restore(
    cluster_id: int, payload: RestoreRequest, db: Session = Depends(get_db)
) -> Job:
    return _submit(db, cluster_id, "restore", payload)


@router.post(
    "/clusters/{cluster_id}/prunes",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_prune(
    cluster_id: int, payload: PruneRequest, db: Session = Depends(get_db)
) -> Job:
    return _submit(db, cluster_id, "prune", payload)


@router.get("/jobs/{job_id}", response_model=JobRead)
def get_job(job_id: int, db: Session = Depends(get_db)) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job
