"""Cluster CRUD and the delete-guard existence checks it needs.

The delete guards (`has_active_job`/`has_enabled_schedule`) live here
rather than in `commands/clusters.py` because they are plain existence
queries against `Job`/`Schedule`, not business logic - `commands/clusters.py`
decides what to do when they return `True` (raise), this module only
answers the question.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...store.models import Cluster, Job, JobStatus, Schedule


def create(
    db: Session,
    *,
    name: str,
    host: str,
    port: int,
    user: str,
    password_encrypted: str,
    default_backend: str,
) -> Cluster:
    cluster = Cluster(
        name=name,
        host=host,
        port=port,
        user=user,
        password_encrypted=password_encrypted,
        default_backend=default_backend,
    )
    db.add(cluster)
    db.flush()
    db.refresh(cluster)
    return cluster


def list_all(db: Session) -> list[Cluster]:
    return list(db.scalars(select(Cluster).order_by(Cluster.id)).all())


def get(db: Session, cluster_id: int) -> Cluster | None:
    return db.get(Cluster, cluster_id)


def update(db: Session, cluster: Cluster, updates: dict) -> Cluster:
    for field, value in updates.items():
        setattr(cluster, field, value)
    db.flush()
    db.refresh(cluster)
    return cluster


def has_active_job(db: Session, cluster_id: int) -> bool:
    return (
        db.scalars(
            select(Job.id)
            .where(
                Job.cluster_id == cluster_id,
                Job.status.in_([JobStatus.PENDING.value, JobStatus.RUNNING.value]),
            )
            .limit(1)
        ).first()
        is not None
    )


def has_enabled_schedule(db: Session, cluster_id: int) -> bool:
    return (
        db.scalars(
            select(Schedule.id)
            .where(Schedule.cluster_id == cluster_id, Schedule.enabled.is_(True))
            .limit(1)
        ).first()
        is not None
    )


def delete(db: Session, cluster: Cluster) -> None:
    db.delete(cluster)
