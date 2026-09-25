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

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...store.crypto import encrypt_password
from ...store.models import Cluster, Job, JobStatus, Schedule
from ..auth import require_api_key
from ..deps import get_db
from ..schemas import ClusterCreate, ClusterRead, ClusterUpdate

router = APIRouter(
    prefix="/clusters", tags=["clusters"], dependencies=[Depends(require_api_key)]
)


def _get_cluster_or_404(db: Session, cluster_id: int) -> Cluster:
    cluster = db.get(Cluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cluster not found")
    return cluster


@router.post("", response_model=ClusterRead, status_code=status.HTTP_201_CREATED)
def create_cluster(payload: ClusterCreate, db: Session = Depends(get_db)) -> Cluster:
    cluster = Cluster(
        name=payload.name,
        host=payload.host,
        port=payload.port,
        user=payload.user,
        password_encrypted=encrypt_password(payload.password),
        database=payload.database,
        repository=payload.repository,
        ops_database=payload.ops_database,
        default_backend=payload.default_backend,
    )
    db.add(cluster)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cluster name '{payload.name}' already exists",
        ) from e
    db.refresh(cluster)
    return cluster


@router.get("", response_model=list[ClusterRead])
def list_clusters(db: Session = Depends(get_db)) -> list[Cluster]:
    return list(db.query(Cluster).order_by(Cluster.id).all())


@router.get("/{cluster_id}", response_model=ClusterRead)
def get_cluster(cluster_id: int, db: Session = Depends(get_db)) -> Cluster:
    return _get_cluster_or_404(db, cluster_id)


@router.patch("/{cluster_id}", response_model=ClusterRead)
def update_cluster(
    cluster_id: int, payload: ClusterUpdate, db: Session = Depends(get_db)
) -> Cluster:
    cluster = _get_cluster_or_404(db, cluster_id)

    updates = payload.model_dump(exclude_unset=True)
    password = updates.pop("password", None)
    for field, value in updates.items():
        setattr(cluster, field, value)
    if password:
        cluster.password_encrypted = encrypt_password(password)

    db.flush()
    db.refresh(cluster)
    return cluster


@router.delete("/{cluster_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cluster(cluster_id: int, db: Session = Depends(get_db)) -> None:
    cluster = _get_cluster_or_404(db, cluster_id)

    active_job = (
        db.query(Job)
        .filter(
            Job.cluster_id == cluster_id,
            Job.status.in_([JobStatus.PENDING.value, JobStatus.RUNNING.value]),
        )
        .first()
    )
    if active_job is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cluster has a job in PENDING or RUNNING state; cannot delete",
        )

    enabled_schedule = (
        db.query(Schedule)
        .filter(Schedule.cluster_id == cluster_id, Schedule.enabled.is_(True))
        .first()
    )
    if enabled_schedule is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cluster has an enabled schedule; disable or delete it before removing the cluster",
        )

    db.delete(cluster)
