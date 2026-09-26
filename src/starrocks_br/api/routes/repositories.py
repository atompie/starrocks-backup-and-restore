"""Repository management routes, scoped to a registered cluster.

Per specs/api-repository-management, repository listing/creation/deletion
are live pass-throughs to the target StarRocks cluster - no local state is
introduced, and submitted S3 credentials are never persisted. Connecting to
the cluster reuses the same `_connect`/`decrypt_password` pattern as
`jobs/handlers.py`.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ... import exceptions, repository, s3_verify
from ...commands import repositories as repository_commands
from ..auth import require_api_key
from ..deps import get_db
from ..schemas import (
    RepositoryCreate,
    RepositoryRead,
    RepositoryVerifyRequest,
    RepositoryVerifyResponse,
)
from ._cluster_connect import connect_or_503 as _connect_or_503
from ._cluster_connect import get_cluster_or_404 as _get_cluster_or_404

router = APIRouter(tags=["repositories"], dependencies=[Depends(require_api_key)])


@router.post("/repositories/verify", response_model=RepositoryVerifyResponse)
def verify_repository(payload: RepositoryVerifyRequest) -> RepositoryVerifyResponse:
    return s3_verify.verify_s3_connection(
        location=payload.location,
        access_key=payload.access_key,
        secret_key=payload.secret_key,
        endpoint=payload.endpoint,
        region=payload.region,
    )


@router.get("/repositories/cluster/{cluster_id}", response_model=list[RepositoryRead])
def list_repositories(cluster_id: int, db: Session = Depends(get_db)) -> list[dict]:
    cluster = _get_cluster_or_404(db, cluster_id)

    database = _connect_or_503(cluster)
    try:
        return repository.list_repositories(database)
    finally:
        database.close()


@router.post(
    "/repositories/cluster/{cluster_id}",
    response_model=RepositoryRead,
    status_code=status.HTTP_201_CREATED,
)
def create_repository(
    cluster_id: int, payload: RepositoryCreate, db: Session = Depends(get_db)
) -> dict:
    cluster = _get_cluster_or_404(db, cluster_id)

    database = _connect_or_503(cluster)
    try:
        return repository_commands.create_repository(
            database,
            cluster.name,
            payload.name,
            payload.location,
            payload.access_key,
            payload.secret_key,
            payload.endpoint,
            payload.region,
        )
    except exceptions.RepositoryAlreadyExistsError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    finally:
        database.close()


@router.delete(
    "/repositories/cluster/{cluster_id}/name/{name}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_repository(cluster_id: int, name: str, db: Session = Depends(get_db)) -> None:
    cluster = _get_cluster_or_404(db, cluster_id)

    database = _connect_or_503(cluster)
    try:
        repository_commands.delete_repository(database, name)
    except exceptions.RepositoryStillHasSnapshotsError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except repository.RepositoryNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{name}' not found on cluster '{cluster.name}'",
        ) from e
    finally:
        database.close()
