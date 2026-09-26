"""Cluster-connection helpers shared by the backup/restore/prune commands.

Private to the `commands` package (leading underscore) - not part of the
command layer's public surface, just factored out to avoid re-implementing
"connect and verify the cluster is ready" identically in three modules.
"""

from .. import db as db_module
from .. import health
from .. import repository as repository_module
from ..store.crypto import decrypt_password
from ..store.models import Cluster


def connect(cluster: Cluster) -> db_module.StarRocksDB:
    return db_module.StarRocksDB(
        host=cluster.host,
        port=cluster.port,
        user=cluster.user,
        password=decrypt_password(cluster.password_encrypted),
        database=None,
    )


def ensure_ready(database: db_module.StarRocksDB, cluster: Cluster, repository: str | None = None) -> None:
    healthy, message = health.check_cluster_health(database)
    if not healthy:
        raise RuntimeError(f"Cluster health check failed: {message}")

    if repository is not None:
        repository_module.ensure_repository(database, repository)
