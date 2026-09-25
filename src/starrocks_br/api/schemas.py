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

"""Request/response schemas for the API.

Per specs/api-cluster-registry, Cluster response models never include the
password or password_encrypted fields - only ClusterCreate/ClusterUpdate
(request bodies) accept a plaintext `password`.
"""

import datetime

from pydantic import BaseModel, ConfigDict, Field


class ClusterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=9030, gt=0, le=65535)
    user: str = Field(min_length=1, max_length=128)
    password: str = Field(default="", description="StarRocks allows an empty password (e.g. local root).")
    database: str = Field(min_length=1, max_length=128)
    repository: str = Field(min_length=1, max_length=128)
    ops_database: str = Field(default="ops", max_length=128)
    default_backend: str = Field(default="thread", max_length=64)


class ClusterUpdate(BaseModel):
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, gt=0, le=65535)
    user: str | None = Field(default=None, min_length=1, max_length=128)
    password: str | None = None
    database: str | None = Field(default=None, min_length=1, max_length=128)
    repository: str | None = Field(default=None, min_length=1, max_length=128)
    ops_database: str | None = Field(default=None, max_length=128)
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
    ops_database: str
    default_backend: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


class JobSubmitRequest(BaseModel):
    group: str | None = None
    name: str | None = None
    baseline_backup: str | None = None
    target_label: str | None = None
    table: str | None = None
    rename_suffix: str = "_restored"
    keep_last: int | None = None
    older_than: str | None = None
    snapshot: str | None = None
    snapshots: str | None = None
    dry_run: bool = False
    backend: str | None = None


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
