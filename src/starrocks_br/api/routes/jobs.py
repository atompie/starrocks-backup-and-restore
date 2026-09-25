# Copyright 2025 deep-bi
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ...jobs.backend import UnknownBackendError, get_registry
from ...store.models import Cluster, Job
from ..auth import require_api_key
from ..deps import get_db
from ..schemas import JobRead, JobSubmitRequest

router = APIRouter(tags=["jobs"], dependencies=[Depends(require_api_key)])


def _get_cluster_or_404(db: Session, cluster_id: int) -> Cluster:
    cluster = db.get(Cluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cluster not found")
    return cluster


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
    db: Session, cluster_id: int, job_type: str, payload: JobSubmitRequest
) -> Job:
    cluster = _get_cluster_or_404(db, cluster_id)
    params = payload.model_dump(exclude={"backend"}, exclude_none=True)
    return submit_job(db, cluster, job_type, params, payload.backend)


@router.post(
    "/clusters/{cluster_id}/backups/full",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_backup_full(
    cluster_id: int, payload: JobSubmitRequest, db: Session = Depends(get_db)
) -> Job:
    return _submit(db, cluster_id, "backup_full", payload)


@router.post(
    "/clusters/{cluster_id}/backups/incremental",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_backup_incremental(
    cluster_id: int, payload: JobSubmitRequest, db: Session = Depends(get_db)
) -> Job:
    return _submit(db, cluster_id, "backup_incremental", payload)


@router.post(
    "/clusters/{cluster_id}/restores",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_restore(
    cluster_id: int, payload: JobSubmitRequest, db: Session = Depends(get_db)
) -> Job:
    return _submit(db, cluster_id, "restore", payload)


@router.post(
    "/clusters/{cluster_id}/prunes",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_prune(
    cluster_id: int, payload: JobSubmitRequest, db: Session = Depends(get_db)
) -> Job:
    return _submit(db, cluster_id, "prune", payload)


@router.get("/jobs/{job_id}", response_model=JobRead)
def get_job(job_id: int, db: Session = Depends(get_db)) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job
