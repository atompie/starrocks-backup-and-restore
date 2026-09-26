from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ... import inventory_groups, repository
from ...commands.jobs import submit_job
from ...jobs.backend import UnknownBackendError
from ...store.models import Cluster, Job
from ..auth import require_api_key
from ..deps import get_db
from ..schemas import (
    BackupFullRequest,
    BackupIncrementalRequest,
    JobRead,
    PruneRequest,
    RestoreRequest,
)
from ._cluster_connect import connect_or_503 as _connect_or_503
from ._cluster_connect import get_cluster_or_404 as _get_cluster_or_404

router = APIRouter(tags=["manual-backups"], dependencies=[Depends(require_api_key)])


def _ensure_repository_exists(cluster: Cluster, repository_name: str) -> None:
    """Synchronously verify `repository_name` exists on `cluster`, raising 404 if not.

    Reuses the same live `SHOW REPOSITORIES` lookup `api-repository-management`
    already uses to list repositories, per specs/api-job-execution "Submitting
    an operation returns immediately with a job".
    """
    database = _connect_or_503(cluster)
    try:
        names = {repo["name"] for repo in repository.list_repositories(database)}
    finally:
        database.close()

    if repository_name not in names:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repository_name}' not found on cluster '{cluster.name}'",
        )


def _submit(
    db: Session, cluster_id: int, job_type: str, payload: BaseModel
) -> Job:
    cluster = _get_cluster_or_404(db, cluster_id)
    params = payload.model_dump(exclude={"backend"})
    try:
        return submit_job(db, cluster, job_type, params, payload.backend)
    except UnknownBackendError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e


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

    if not inventory_groups.group_exists(db, cluster_id, payload.group_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory group id {payload.group_id} not found on cluster '{cluster.name}'",
        )

    _ensure_repository_exists(cluster, payload.repository)

    params = payload.model_dump(exclude={"backend"})
    try:
        return submit_job(db, cluster, job_type, params, payload.backend)
    except UnknownBackendError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e


@router.post(
    "/backup/manual/full/cluster/{cluster_id}",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_backup_full(
    cluster_id: int, payload: BackupFullRequest, db: Session = Depends(get_db)
) -> Job:
    return _submit_backup_job(db, cluster_id, "backup_full", payload)


@router.post(
    "/backup/manual/incremental/cluster/{cluster_id}",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_backup_incremental(
    cluster_id: int, payload: BackupIncrementalRequest, db: Session = Depends(get_db)
) -> Job:
    return _submit_backup_job(db, cluster_id, "backup_incremental", payload)


@router.post(
    "/backup/manual/restore/cluster/{cluster_id}",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_restore(
    cluster_id: int, payload: RestoreRequest, db: Session = Depends(get_db)
) -> Job:
    return _submit(db, cluster_id, "restore", payload)


def _submit_prune_job(db: Session, cluster_id: int, payload: PruneRequest) -> Job:
    """Submit a prune job, failing fast on an unknown group.

    Per specs/api-job-execution "Prune requests specify exactly one pruning
    strategy", every prune request is scoped to an inventory group; an
    unknown group is rejected synchronously with 404, mirroring the same
    check on backup submission.
    """
    cluster = _get_cluster_or_404(db, cluster_id)

    if not inventory_groups.group_exists(db, cluster_id, payload.group_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory group id {payload.group_id} not found on cluster '{cluster.name}'",
        )

    params = payload.model_dump(exclude={"backend"})
    try:
        return submit_job(db, cluster, "prune", params, payload.backend)
    except UnknownBackendError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e


@router.post(
    "/backup/manual/prune/cluster/{cluster_id}",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_prune(
    cluster_id: int, payload: PruneRequest, db: Session = Depends(get_db)
) -> Job:
    return _submit_prune_job(db, cluster_id, payload)


@router.get("/job/{job_id}", response_model=JobRead)
def get_job(job_id: int, db: Session = Depends(get_db)) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job
