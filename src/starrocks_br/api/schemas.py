"""Request/response schemas for the API.

Per specs/api-cluster-registry, Cluster response models never include the
password or password_encrypted fields - only ClusterCreate/ClusterUpdate
(request bodies) accept a plaintext `password`.
"""

import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClusterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=9030, gt=0, le=65535)
    user: str = Field(min_length=1, max_length=128)
    password: str = Field(default="", description="StarRocks allows an empty password (e.g. local root).")
    database: str = Field(min_length=1, max_length=128)
    repository: str = Field(min_length=1, max_length=128)
    default_backend: str = Field(default="thread", max_length=64)


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
    database: str | None = Field(default=None, min_length=1, max_length=128)
    repository: str | None = Field(default=None, min_length=1, max_length=128)
    default_backend: str | None = Field(default=None, max_length=64)


class ClusterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    host: str
    port: int
    user: str
    database: str
    repository: str
    default_backend: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


class BackupFullRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group: str = Field(min_length=1, max_length=128)
    name: str | None = None
    backend: str | None = None


class BackupIncrementalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group: str = Field(min_length=1, max_length=128)
    name: str | None = None
    baseline_backup: str | None = None
    backend: str | None = None


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_label: str = Field(min_length=1)
    group: str | None = None
    table: str | None = None
    rename_suffix: str = "_restored"
    backend: str | None = None

    @model_validator(mode="after")
    def _check_group_and_table_not_both_set(self) -> "RestoreRequest":
        if self.group and self.table:
            raise ValueError("Cannot specify both 'group' and 'table'")
        return self


class PruneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group: str | None = None
    keep_last: int | None = Field(default=None, gt=0)
    older_than: str | None = None
    snapshot: str | None = None
    snapshots: str | None = None
    dry_run: bool = False
    backend: str | None = None

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
    cluster_id: int
    job_type: str = Field(pattern="^(backup_full|backup_incremental)$")
    group_name: str = Field(min_length=1, max_length=128)
    cadence: str = Field(min_length=1, max_length=128)
    backend: str | None = None
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    job_type: str | None = Field(default=None, pattern="^(backup_full|backup_incremental)$")
    group_name: str | None = Field(default=None, min_length=1, max_length=128)
    cadence: str | None = Field(default=None, min_length=1, max_length=128)
    backend: str | None = None
    enabled: bool | None = None


class ScheduleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cluster_id: int
    job_type: str
    group_name: str
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
    name: str
    tables: list[InventoryMembershipRead]
