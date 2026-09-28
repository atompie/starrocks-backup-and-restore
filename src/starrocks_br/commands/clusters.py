"""The single implementation of cluster management use cases.

Business rules (currently: the delete-guard checks) live here; the
SQLAlchemy access they and plain CRUD need lives in
`dal/metadata/clusters.py`.
"""

from sqlalchemy.orm import Session

from ..dal.metadata import clusters as clusters_dal
from ..exceptions import ClusterHasActiveJobError, ClusterHasEnabledScheduleError
from ..store.crypto import encrypt_password
from ..store.models import Cluster


def create_cluster(
    db: Session,
    *,
    name: str,
    host: str,
    port: int,
    user: str,
    password: str,
    default_backend: str,
) -> Cluster:
    return clusters_dal.create(
        db,
        name=name,
        host=host,
        port=port,
        user=user,
        password_encrypted=encrypt_password(password),
        default_backend=default_backend,
    )


def list_clusters(db: Session) -> list[Cluster]:
    return clusters_dal.list_all(db)


def get_cluster(db: Session, cluster_id: int) -> Cluster | None:
    return clusters_dal.get(db, cluster_id)


def update_cluster(db: Session, cluster: Cluster, updates: dict) -> Cluster:
    """Apply `updates` (a `ClusterUpdate.model_dump(exclude_unset=True)` dict) to `cluster`.

    A `password` key is re-encrypted into `password_encrypted` before the
    write; every other key is passed straight through.
    """
    updates = dict(updates)
    if "password" in updates:
        updates["password_encrypted"] = encrypt_password(updates.pop("password"))
    return clusters_dal.update(db, cluster, updates)


def delete_cluster(session: Session, cluster: Cluster) -> None:
    """Delete `cluster`, refusing when it has an active job or an enabled schedule."""
    if clusters_dal.has_active_job(session, cluster.id):
        raise ClusterHasActiveJobError(cluster.id)

    if clusters_dal.has_enabled_schedule(session, cluster.id):
        raise ClusterHasEnabledScheduleError(cluster.id)

    clusters_dal.delete(session, cluster)
