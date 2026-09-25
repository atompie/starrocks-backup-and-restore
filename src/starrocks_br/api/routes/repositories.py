"""Repository management routes, scoped to a registered cluster.

Per specs/api-repository-management, repository listing/creation/deletion
are live pass-throughs to the target StarRocks cluster - no local state is
introduced, and submitted S3 credentials are never persisted. Connecting to
the cluster reuses the same `_connect`/`decrypt_password` pattern as
`jobs/handlers.py`.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ... import repository, s3_verify
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


@router.get("/cluster/{cluster_id}/repositories", response_model=list[RepositoryRead])
def list_repositories(cluster_id: int, db: Session = Depends(get_db)) -> list[dict]:
    cluster = _get_cluster_or_404(db, cluster_id)

    database = _connect_or_503(cluster)
    try:
        return repository.list_repositories(database)
    finally:
        database.close()


@router.post(
    "/cluster/{cluster_id}/repositories",
    response_model=RepositoryRead,
    status_code=status.HTTP_201_CREATED,
)
def create_repository(
    cluster_id: int, payload: RepositoryCreate, db: Session = Depends(get_db)
) -> dict:
    cluster = _get_cluster_or_404(db, cluster_id)

    database = _connect_or_503(cluster)
    try:
        command = repository.build_create_s3_repository_command(
            name=payload.name,
            location=payload.location,
            access_key=payload.access_key,
            secret_key=payload.secret_key,
            endpoint=payload.endpoint,
            region=payload.region,
        )
        try:
            database.execute(command)
        except Exception as e:
            # StarRocks' actual wording is "already exist" (verified against a
            # live cluster during the manual smoke test), not "already exists".
            if "already exist" in str(e).lower():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Repository '{payload.name}' already exists on cluster '{cluster.name}'",
                ) from e
            raise

        for repo in repository.list_repositories(database):
            if repo["name"] == payload.name:
                return repo

        # StarRocks accepted the CREATE REPOSITORY statement but does not
        # (yet) list it back - report what we know without guessing fields.
        return {
            "name": payload.name,
            "location": payload.location,
            "broker": None,
            "is_read_only": False,
            "error": None,
        }
    finally:
        database.close()


@router.delete("/cluster/{cluster_id}/repositories/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_repository(cluster_id: int, name: str, db: Session = Depends(get_db)) -> None:
    cluster = _get_cluster_or_404(db, cluster_id)

    database = _connect_or_503(cluster)
    try:
        try:
            if repository.has_snapshots(database, name):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Repository '{name}' still holds snapshot data; cannot delete",
                )
        except repository.RepositoryNotFoundError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Repository '{name}' not found on cluster '{cluster.name}'",
            ) from e

        repository.drop_repository(database, name)
    finally:
        database.close()
