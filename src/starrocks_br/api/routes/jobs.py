import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ... import exceptions
from ...commands.jobs import get_job as _get_job_command
from ...commands.jobs import get_job_history as _get_job_history_command
from ...commands.jobs import get_job_references as _get_job_references_command
from ...commands.jobs import list_jobs, submit_job
from ...commands.restore import submit_restore_job
from ...dal.metadata import inventory_groups
from ...jobs.backend import UnknownBackendError
from ...store.models import Job
from ..auth import require_api_key
from ..deps import get_db
from ..schemas import BackupReferenceRead, HistoryEntryRead, JobRead, PruneRequest, RestoreRequest
from ._cluster_connect import get_cluster_or_404 as _get_cluster_or_404

router = APIRouter(tags=["manual-backups"], dependencies=[Depends(require_api_key)])

_DEFAULT_BACKUP_JOB_TYPES = ["backup_full", "backup_incremental"]


@router.post(
    "/backup/manual/restore/cluster/{cluster_id}",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_restore(
    cluster_id: int, payload: RestoreRequest, db: Session = Depends(get_db)
) -> Job:
    cluster = _get_cluster_or_404(db, cluster_id)
    params = payload.model_dump(exclude={"backend"})
    try:
        return submit_restore_job(db, cluster, params, payload.backend)
    except exceptions.RestoreSourcePendingDeletionError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except UnknownBackendError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e


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
    job = _get_job_command(db, job_id)
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
    job = _get_job_command(db, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    rows = _get_job_history_command(db, job.job_type, job_id)
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


@router.get("/job/{job_id}/references", response_model=list[BackupReferenceRead])
def get_job_references(job_id: int, db: Session = Depends(get_db)) -> list[BackupReferenceRead]:
    """Return a backup job's recorded references.

    Per specs/api-job-execution "A job's backup references can be retrieved": references are
    only recorded once the job's StarRocks operation reaches `FINISHED`, so a still-`RUNNING` or
    `FAILED` job has an empty list rather than a 404.
    """
    job = _get_job_command(db, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    rows = _get_job_references_command(db, job_id)
    return [
        BackupReferenceRead(
            id=row.id,
            job_id=row.job_id,
            repository=row.repository,
            snapshot_label=row.snapshot_label,
            snapshot_timestamp=row.snapshot_timestamp,
            database=row.database_name,
            table=row.table_name,
            partition=row.partition_name,
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
    schedule_id: int | None = Query(default=None),
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
        schedule_id=schedule_id,
        limit=limit,
        offset=offset,
    )
