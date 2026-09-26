"""Shared cluster-lookup and connection helpers for route modules.

Extracted from `repositories.py`, `jobs.py`, and `clusters.py`, which each
defined their own copy of `_get_cluster_or_404`, and `repositories.py`,
which also defined `_connect`/`_connect_or_503`. Route modules import from
here instead of keeping their own copies.
"""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ... import db as db_module
from ... import repository as repository_module
from ...store.crypto import decrypt_password
from ...store.models import Cluster
from ..schemas import ClusterVerifyResponse

VERIFY_CONNECT_TIMEOUT_SECONDS = 5


def get_cluster_or_404(db: Session, cluster_id: int) -> Cluster:
    cluster = db.get(Cluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cluster not found")
    return cluster


def connect(cluster: Cluster) -> db_module.StarRocksDB:
    return db_module.StarRocksDB(
        host=cluster.host,
        port=cluster.port,
        user=cluster.user,
        password=decrypt_password(cluster.password_encrypted),
        database=None,
    )


def connect_or_503(cluster: Cluster) -> db_module.StarRocksDB:
    """Return a connected `StarRocksDB`, translating connection failures into 503.

    Callers must `database.close()` when done - the caller, not this helper,
    owns the connection lifecycle so it can run several operations on it.
    """
    database = connect(cluster)
    try:
        database.connect()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not connect to cluster '{cluster.name}': {e}",
        ) from e
    return database


def ensure_repository_exists(cluster: Cluster, repository_name: str) -> None:
    """Synchronously verify `repository_name` exists on `cluster`, raising 404 if not.

    Reuses the same live `SHOW REPOSITORIES` lookup `api-repository-management`
    already uses to list repositories. Shared by backup-job submission
    (`jobs.py`) and schedule creation/update (`schedules.py`), per
    specs/api-job-execution and specs/api-scheduling.
    """
    database = connect_or_503(cluster)
    try:
        names = {repo["name"] for repo in repository_module.list_repositories(database)}
    finally:
        database.close()

    if repository_name not in names:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repository_name}' not found on cluster '{cluster.name}'",
        )


def verify_connection(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str | None,
    *,
    timeout: int = VERIFY_CONNECT_TIMEOUT_SECONDS,
) -> ClusterVerifyResponse:
    """Attempt a real connection and report success/failure without raising.

    Reused by both `POST /clusters/verify` and `GET /cluster/{id}/verify` so
    the two endpoints share one connection-testing code path. Unlike
    `connect_or_503`, a failed connection here is an expected outcome, not
    an error - it always returns a `ClusterVerifyResponse`, never a 503.
    """
    connection = db_module.StarRocksDB(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        connect_timeout=timeout,
    )
    try:
        connection.connect()
    except Exception as e:
        message = str(e)
        if password and password in message:
            message = message.replace(password, "***")
        return ClusterVerifyResponse(success=False, message=f"Connection failed: {message}")
    else:
        return ClusterVerifyResponse(success=True, message="Connection successful")
    finally:
        connection.close()
