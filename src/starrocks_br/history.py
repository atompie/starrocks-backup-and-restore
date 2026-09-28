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

import json

from sqlalchemy.orm import Session, sessionmaker

from .store.models import BackupHistory, RestoreHistory

# Terminal rows are always appended, bypassing the unchanged-state dedup
# check - StarRocks itself never reports these two values, so they can never
# collide with a StarRocks-native state already recorded for the job.
TERMINAL_STATUSES = {"SUCCESS", "FAILED"}


def _append_event(
    model: type[BackupHistory] | type[RestoreHistory],
    session_factory: sessionmaker[Session],
    job_id: int,
    status: str,
    message: str | None,
    details: dict | None,
) -> None:
    with session_factory() as session:
        if status not in TERMINAL_STATUSES:
            last = (
                session.query(model)
                .filter_by(job_id=job_id)
                .order_by(model.id.desc())
                .first()
            )
            if last is not None and last.status == status:
                return

        session.add(
            model(
                job_id=job_id,
                status=status,
                message=message,
                details_json=json.dumps(details) if details is not None else None,
            )
        )
        session.commit()


def append_backup_event(
    session_factory: sessionmaker[Session],
    job_id: int,
    status: str,
    message: str | None = None,
    details: dict | None = None,
) -> None:
    """Append a state-change row to a backup job's history, if it isn't a duplicate.

    Opens its own short-lived session (see AGENTS.md's parallel-execution
    rules). A no-op when `status` matches the job's most recently recorded
    row, except for a terminal `SUCCESS`/`FAILED` row, which is always
    appended.
    """
    _append_event(BackupHistory, session_factory, job_id, status, message, details)


def append_restore_event(
    session_factory: sessionmaker[Session],
    job_id: int,
    status: str,
    message: str | None = None,
    details: dict | None = None,
) -> None:
    """Append a state-change row to a restore job's history, if it isn't a duplicate.

    Mirrors `append_backup_event` for `restore_history`.
    """
    _append_event(RestoreHistory, session_factory, job_id, status, message, details)
