"""Request/response schemas for the API.

Per specs/api-cluster-registry, Cluster response models never include the
password or password_encrypted fields - only ClusterCreate/ClusterUpdate
(request bodies) accept a plaintext `password`.
"""

import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Backend = Literal["thread", "job"]


class ClusterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=9030, gt=0, le=65535)
    user: str = Field(min_length=1, max_length=128)
    password: str = Field(default="", description="StarRocks allows an empty password (e.g. local root).")
    default_backend: Backend = "thread"


class ClusterVerifyRequest(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=9030, gt=0, le=65535)
    user: str = Field(min_length=1, max_length=128)
    password: str = Field(default="", description="StarRocks allows an empty password (e.g. local root).")
    database: str | None = Field(default=None, min_length=1, max_length=128)


class ClusterVerifyResponse(BaseModel):
    success: bool
    message: str


class ClusterUpdate(BaseModel):
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, gt=0, le=65535)
    user: str | None = Field(default=None, min_length=1, max_length=128)
    password: str | None = None
    default_backend: Backend | None = None


class ClusterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    host: str
    port: int
    user: str
    default_backend: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


class BackupFullRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: int
    repository: str = Field(min_length=1, max_length=128)
    name: str | None = None
    backend: Backend | None = None


class BackupIncrementalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: int
    repository: str = Field(min_length=1, max_length=128)
    name: str | None = None
    baseline_backup: str | None = None
    backend: Backend | None = None


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_label: str = Field(min_length=1)
    group_id: int | None = None
    table: str | None = None
    database: str | None = Field(default=None, min_length=1, max_length=128)
    rename_suffix: str = "_restored"
    backend: Backend | None = None

    @model_validator(mode="after")
    def _check_group_and_table_not_both_set(self) -> "RestoreRequest":
        if self.group_id and self.table:
            raise ValueError("Cannot specify both 'group_id' and 'table'")
        if self.table and not self.database:
            raise ValueError("'database' is required when 'table' is specified")
        return self


class PruneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: int
    keep_last: int | None = Field(default=None, gt=0)
    older_than: str | None = None
    snapshot: str | None = None
    snapshots: str | None = None
    dry_run: bool = False
    backend: Backend | None = None

    @model_validator(mode="after")
    def _check_exactly_one_strategy(self) -> "PruneRequest":
        specified = [v for v in (self.keep_last, self.older_than, self.snapshot, self.snapshots)
                     if v is not None]
        if len(specified) != 1:
            raise ValueError(
                "Exactly one of 'keep_last', 'older_than', 'snapshot', 'snapshots' must be provided"
            )
        return self


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cluster_id: int
    job_type: str
    backend: str
    status: str
    progress_pct: int | None
    state_detail: str | None
    error_message: str | None
    created_at: datetime.datetime
    started_at: datetime.datetime | None
    finished_at: datetime.datetime | None


class ScheduleCreate(BaseModel):
    job_type: str = Field(pattern="^(backup_full|backup_incremental)$")
    inventory_group_id: int
    cadence: str = Field(min_length=1, max_length=128)
    backend: Backend | None = None
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    job_type: str | None = Field(default=None, pattern="^(backup_full|backup_incremental)$")
    inventory_group_id: int | None = None
    cadence: str | None = Field(default=None, min_length=1, max_length=128)
    backend: Backend | None = None
    enabled: bool | None = None


class ScheduleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cluster_id: int
    job_type: str
    inventory_group_id: int
    cadence: str
    backend: str | None
    enabled: bool
    next_run_at: datetime.datetime
    last_run_job_id: int | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class RunDueResponse(BaseModel):
    triggered_job_ids: list[int]
    triggered_count: int


class RepositoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    location: str = Field(min_length=1)
    access_key: str = Field(min_length=1)
    secret_key: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    region: str | None = None


class RepositoryRead(BaseModel):
    name: str
    location: str | None
    broker: str | None
    is_read_only: bool
    error: str | None


class RepositoryVerifyRequest(BaseModel):
    location: str = Field(min_length=1)
    access_key: str = Field(min_length=1)
    secret_key: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    region: str | None = None


class RepositoryVerifyResponse(BaseModel):
    success: bool
    message: str


class InventoryGroupSummary(BaseModel):
    id: int
    name: str
    table_count: int


class InventoryMembershipCreate(BaseModel):
    database: str = Field(min_length=1, max_length=128)
    table: str = Field(
        min_length=1, max_length=128, description="Table name, or '*' for all tables in the database"
    )


class InventoryGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    tables: list[InventoryMembershipCreate] = Field(min_length=1)


class InventoryMembershipRead(BaseModel):
    database: str
    table: str
    created_at: str
    updated_at: str


class InventoryGroupRead(BaseModel):
    id: int
    name: str
    tables: list[InventoryMembershipRead]
