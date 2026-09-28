import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...dal.metadata import inventory_groups
from ...commands.jobs import list_jobs, submit_job
from ...jobs.backend import UnknownBackendError
from ...store.models import BackupHistory, Job, RestoreHistory
from ..auth import require_api_key
from ..deps import get_db
from ..schemas import (
    BackupFullRequest,
    BackupIncrementalRequest,
    HistoryEntryRead,
    JobRead,
    PruneRequest,
    RestoreRequest,
)
from ._cluster_connect import ensure_repository_exists as _ensure_repository_exists
from ._cluster_connect import get_cluster_or_404 as _get_cluster_or_404

router = APIRouter(tags=["manual-backups"], dependencies=[Depends(require_api_key)])

_DEFAULT_BACKUP_JOB_TYPES = ["backup_full", "backup_incremental"]


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


@router.get("/job/{job_id}/history", response_model=list[HistoryEntryRead])
def get_job_history(job_id: int, db: Session = Depends(get_db)) -> list[HistoryEntryRead]:
    """Return a backup/restore job's append-only execution history, oldest first.

    Per specs/api-job-execution "A job's execution history can be retrieved": the log
    table is chosen by the job's `job_type`; a job type with no log table (e.g. `prune`)
    has an empty history rather than a 404.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    if job.job_type in _DEFAULT_BACKUP_JOB_TYPES:
        model = BackupHistory
    elif job.job_type == "restore":
        model = RestoreHistory
    else:
        return []

    rows = db.scalars(select(model).where(model.job_id == job_id).order_by(model.ts.asc())).all()
    return [
        HistoryEntryRead(
            id=row.id,
            job_id=row.job_id,
            ts=row.ts,
            status=row.status,
            message=row.message,
            details=json.loads(row.details_json) if row.details_json is not None else None,
        )
        for row in rows
    ]


@router.get("/backup/history/cluster/{cluster_id}", response_model=list[JobRead])
def list_backup_history(
    cluster_id: int,
    job_type: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    job_id: int | None = Query(default=None),
    group_id: int | None = Query(default=None),
    limit: int = Query(default=50),
    offset: int = Query(default=0),
    db: Session = Depends(get_db),
) -> list[Job]:
    _get_cluster_or_404(db, cluster_id)
    return list_jobs(
        db,
        cluster_id,
        job_type=job_type if job_type is not None else _DEFAULT_BACKUP_JOB_TYPES,
        status=status_filter,
        job_id=job_id,
        group_id=group_id,
        limit=limit,
        offset=offset,
    )
