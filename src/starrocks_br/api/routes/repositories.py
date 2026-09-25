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

"""Repository management routes, scoped to a registered cluster.

Per specs/api-repository-management, repository listing/creation/deletion
are live pass-throughs to the target StarRocks cluster - no local state is
introduced, and submitted S3 credentials are never persisted. Connecting to
the cluster reuses the same `_connect`/`decrypt_password` pattern as
`jobs/handlers.py`.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ... import db as db_module
from ... import repository
from ...store.crypto import decrypt_password
from ...store.models import Cluster
from ..auth import require_api_key
from ..deps import get_db
from ..schemas import RepositoryCreate, RepositoryRead

router = APIRouter(tags=["repositories"], dependencies=[Depends(require_api_key)])


def _get_cluster_or_404(db: Session, cluster_id: int) -> Cluster:
    cluster = db.get(Cluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cluster not found")
    return cluster


def _connect(cluster: Cluster) -> db_module.StarRocksDB:
    return db_module.StarRocksDB(
        host=cluster.host,
        port=cluster.port,
        user=cluster.user,
        password=decrypt_password(cluster.password_encrypted),
        database=cluster.database,
    )


def _connect_or_503(cluster: Cluster) -> db_module.StarRocksDB:
    """Return a connected `StarRocksDB`, translating connection failures into 503.

    Callers must `database.close()` when done - the caller, not this helper,
    owns the connection lifecycle so it can run several operations on it.
    """
    database = _connect(cluster)
    try:
        database.connect()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not connect to cluster '{cluster.name}': {e}",
        ) from e
    return database


@router.get("/clusters/{cluster_id}/repositories", response_model=list[RepositoryRead])
def list_repositories(cluster_id: int, db: Session = Depends(get_db)) -> list[dict]:
    cluster = _get_cluster_or_404(db, cluster_id)

    database = _connect_or_503(cluster)
    try:
        return repository.list_repositories(database)
    finally:
        database.close()


@router.post(
    "/clusters/{cluster_id}/repositories",
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


@router.delete("/clusters/{cluster_id}/repositories/{name}", status_code=status.HTTP_204_NO_CONTENT)
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
