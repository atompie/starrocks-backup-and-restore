from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ... import exceptions
from ...commands.clusters import delete_cluster as _delete_cluster_command
from ...store.crypto import EncryptionKeyMissingError, decrypt_password, encrypt_password
from ...store.models import Cluster
from ..auth import require_api_key
from ..deps import get_db
from ..schemas import (
    ClusterCreate,
    ClusterRead,
    ClusterUpdate,
    ClusterVerifyRequest,
    ClusterVerifyResponse,
)
from ._cluster_connect import get_cluster_or_404 as _get_cluster_or_404
from ._cluster_connect import verify_connection as _verify_connection

cluster_router = APIRouter(
    prefix="/cluster", tags=["clusters"], dependencies=[Depends(require_api_key)]
)
clusters_router = APIRouter(
    prefix="/clusters", tags=["clusters"], dependencies=[Depends(require_api_key)]
)


@cluster_router.post("", response_model=ClusterRead, status_code=status.HTTP_201_CREATED)
def create_cluster(payload: ClusterCreate, db: Session = Depends(get_db)) -> Cluster:
    cluster = Cluster(
        name=payload.name,
        host=payload.host,
        port=payload.port,
        user=payload.user,
        password_encrypted=encrypt_password(payload.password),
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


@clusters_router.post("/verify", response_model=ClusterVerifyResponse)
def verify_connection_params(payload: ClusterVerifyRequest) -> ClusterVerifyResponse:
    return _verify_connection(
        host=payload.host,
        port=payload.port,
        user=payload.user,
        password=payload.password,
        database=payload.database,
    )


@clusters_router.get("", response_model=list[ClusterRead])
def list_clusters(db: Session = Depends(get_db)) -> list[Cluster]:
    return list(db.query(Cluster).order_by(Cluster.id).all())


@cluster_router.get("/{cluster_id}", response_model=ClusterRead)
def get_cluster(cluster_id: int, db: Session = Depends(get_db)) -> Cluster:
    return _get_cluster_or_404(db, cluster_id)


@cluster_router.get("/{cluster_id}/verify", response_model=ClusterVerifyResponse)
def verify_cluster(cluster_id: int, db: Session = Depends(get_db)) -> ClusterVerifyResponse:
    cluster = _get_cluster_or_404(db, cluster_id)
    try:
        password = decrypt_password(cluster.password_encrypted)
    except (ValueError, EncryptionKeyMissingError) as e:
        return ClusterVerifyResponse(success=False, message=f"Connection failed: {e}")
    return _verify_connection(
        host=cluster.host,
        port=cluster.port,
        user=cluster.user,
        password=password,
        database=None,
    )


@cluster_router.patch("/{cluster_id}", response_model=ClusterRead)
def update_cluster(
    cluster_id: int, payload: ClusterUpdate, db: Session = Depends(get_db)
) -> Cluster:
    cluster = _get_cluster_or_404(db, cluster_id)

    updates = payload.model_dump(exclude_unset=True)
    password_provided = "password" in updates
    password = updates.pop("password", None)
    for field, value in updates.items():
        setattr(cluster, field, value)
    if password_provided:
        cluster.password_encrypted = encrypt_password(password)

    db.flush()
    db.refresh(cluster)
    return cluster


@cluster_router.delete("/{cluster_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cluster(cluster_id: int, db: Session = Depends(get_db)) -> None:
    cluster = _get_cluster_or_404(db, cluster_id)

    try:
        _delete_cluster_command(db, cluster)
    except exceptions.ClusterHasActiveJobError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except exceptions.ClusterHasEnabledScheduleError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
