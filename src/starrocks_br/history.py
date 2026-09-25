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

import datetime

from sqlalchemy.orm import Session

from . import logger
from .store.models import BackupHistory, RestoreHistory


def _parse_timestamp(value: str | None) -> datetime.datetime | None:
    """Parse a 'YYYY-MM-DD HH:MM:SS' cluster-local timestamp string, if present."""
    if not value or value == "NULL":
        return None
    return datetime.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def log_backup(session: Session, cluster_id: int, entry: dict[str, str | None]) -> None:
    """Write a backup history entry to the backup_history table.

    Expected keys in entry:
      - label
      - backup_type (incremental|full)
      - status (FINISHED|FAILED|CANCELLED)
      - repository
      - started_at (YYYY-MM-DD HH:MM:SS)
      - finished_at (YYYY-MM-DD HH:MM:SS)
      - error_message (nullable)
    """
    try:
        session.add(
            BackupHistory(
                cluster_id=cluster_id,
                label=entry.get("label", ""),
                backup_type=entry.get("backup_type", ""),
                status=entry.get("status", ""),
                repository=entry.get("repository", ""),
                started_at=_parse_timestamp(entry.get("started_at")),
                finished_at=_parse_timestamp(entry.get("finished_at")),
                error_message=entry.get("error_message"),
            )
        )
        session.flush()
    except Exception as e:
        logger.error(f"Failed to log backup history: {str(e)}")
        raise


def log_restore(session: Session, cluster_id: int, entry: dict[str, str | None]) -> None:
    """Write a restore history entry to the restore_history table.

    Expected keys in entry:
      - job_id
      - backup_label
      - restore_type (partition|table|database)
      - status (FINISHED|FAILED|CANCELLED)
      - repository
      - started_at (YYYY-MM-DD HH:MM:SS)
      - finished_at (YYYY-MM-DD HH:MM:SS)
      - error_message (nullable)
      - verification_checksum (optional)
    """
    try:
        session.add(
            RestoreHistory(
                cluster_id=cluster_id,
                job_id=entry.get("job_id", ""),
                backup_label=entry.get("backup_label", ""),
                restore_type=entry.get("restore_type", ""),
                status=entry.get("status", ""),
                repository=entry.get("repository", ""),
                started_at=_parse_timestamp(entry.get("started_at")),
                finished_at=_parse_timestamp(entry.get("finished_at")),
                error_message=entry.get("error_message"),
                verification_checksum=entry.get("verification_checksum"),
            )
        )
        session.flush()
    except Exception as e:
        logger.error(f"Failed to log restore history: {str(e)}")
        raise
