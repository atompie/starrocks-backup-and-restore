"""SQLAlchemy models for the API's own metadata store.

This store tracks which clusters are registered with the API, the jobs
submitted against them, recurring schedules, and (since the move-ops-tables-
to-sqlite change) each cluster's own backup/restore bookkeeping - table
inventory, backup/restore history, the job-concurrency lock table, and
backup partition manifests - all scoped by `cluster_id` instead of living in
a per-cluster StarRocks database. Column types are chosen to be portable
between SQLite (the default) and MySQL/Postgres (via DATABASE_URL) without
migration changes.
"""

import datetime
import enum

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class JobType(str, enum.Enum):
    BACKUP_FULL = "backup_full"
    BACKUP_INCREMENTAL = "backup_incremental"
    RESTORE = "restore"
    PRUNE = "prune"


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class Cluster(Base):
    __tablename__ = "clusters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    user: Mapped[str] = mapped_column(String(128), nullable=False)
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    default_backend: Mapped[str] = mapped_column(String(64), nullable=False, default="thread")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    jobs: Mapped[list["Job"]] = relationship(back_populates="cluster")
    schedules: Mapped[list["Schedule"]] = relationship(back_populates="cluster")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.id"), nullable=False, index=True)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    group_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    params_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    backend: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=JobStatus.PENDING.value, index=True
    )
    progress_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state_detail: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    repository: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    cluster: Mapped["Cluster"] = relationship(back_populates="jobs")


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.id"), nullable=False, index=True)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    inventory_group_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_groups.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    repository: Mapped[str] = mapped_column(String(128), nullable=False)
    cadence: Mapped[str] = mapped_column(String(128), nullable=False)
    backend: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    next_run_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_run_job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    cluster: Mapped["Cluster"] = relationship(back_populates="schedules")


class InventoryGroup(Base):
    __tablename__ = "inventory_groups"
    __table_args__ = (UniqueConstraint("cluster_id", "name", name="uq_inventory_groups_cluster_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class TableInventory(Base):
    __tablename__ = "table_inventory"
    __table_args__ = (
        UniqueConstraint(
            "cluster_id",
            "inventory_group_id",
            "database_name",
            "table_name",
            name="uq_table_inventory_membership",
        ),
        Index("ix_table_inventory_cluster_group", "cluster_id", "inventory_group_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False, index=True)
    inventory_group_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_groups.id", ondelete="CASCADE"), nullable=False
    )
    database_name: Mapped[str] = mapped_column(String(128), nullable=False)
    table_name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class BackupHistory(Base):
    """Append-only log of a backup job's StarRocks-reported states.

    One row per distinct state (see `history.append_backup_event`); never
    updated or deleted after being written.
    """

    __tablename__ = "backup_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class RestoreHistory(Base):
    """Append-only log of a restore job's StarRocks-reported states.

    One row per distinct state (see `history.append_restore_event`); never
    updated or deleted after being written.
    """

    __tablename__ = "restore_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class RunStatus(Base):
    __tablename__ = "run_status"
    __table_args__ = (
        UniqueConstraint("cluster_id", "scope", "label", name="uq_run_status_cluster_scope_label"),
        Index("ix_run_status_cluster_scope_state", "cluster_id", "scope", "state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BackupPartition(Base):
    __tablename__ = "backup_partitions"
    __table_args__ = (
        UniqueConstraint("cluster_id", "key_hash", name="uq_backup_partitions_cluster_key_hash"),
        Index("ix_backup_partitions_cluster_label", "cluster_id", "label"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    database_name: Mapped[str] = mapped_column(String(128), nullable=False)
    table_name: Mapped[str] = mapped_column(String(128), nullable=False)
    partition_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
