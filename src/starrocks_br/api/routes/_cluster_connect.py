"""Shared cluster-lookup and connection helpers for route modules.

Extracted from `repositories.py`, `jobs.py`, and `clusters.py`, which each
defined their own copy of `_get_cluster_or_404`, and `repositories.py`,
which also defined `_connect`/`_connect_or_503`. Route modules import from
here instead of keeping their own copies.
"""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ... import db as db_module
from ...store.crypto import decrypt_password
from ...store.models import Cluster


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
        database=cluster.database,
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
