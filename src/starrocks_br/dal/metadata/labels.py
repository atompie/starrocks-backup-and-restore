from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...store.models import Job


def determine_backup_label(
    session: Session,
    cluster_id: int,
    backup_type: Literal["incremental", "full"],
    database_name: str,
    custom_name: str | None = None,
) -> str:
    """Determine a unique backup label for the given parameters.

    This is the single entry point for all backup label generation. It handles both
    custom names and auto-generated date-based labels, ensuring uniqueness by checking
    previously-assigned labels on this cluster's `Job` rows (see design.md of
    add-job-history-log: `Job.label` is the backup catalog `backup_history` used to serve).

    Args:
        session: SQLite metastore session
        cluster_id: Cluster this backup belongs to
        backup_type: Type of backup (incremental, full)
        database_name: Name of the database being backed up
        custom_name: Optional custom name for the backup. If provided, this becomes
                    the base label. If None, generates a date-based label.

    Returns:
        Unique label string that doesn't conflict with existing backups
    """
    if custom_name:
        base_label = custom_name
    else:
        today = datetime.now().strftime("%Y%m%d")
        base_label = f"{database_name}_{today}_{backup_type}"

    try:
        rows = session.scalars(
            select(Job.label)
            .where(Job.cluster_id == cluster_id, Job.label.like(f"{base_label}%"))
            .order_by(Job.label)
        )
        existing_labels = list(rows)
    except Exception:
        existing_labels = []

    if base_label not in existing_labels:
        return base_label

    retry_count = 1
    while True:
        candidate_label = f"{base_label}_r{retry_count}"
        if candidate_label not in existing_labels:
            return candidate_label
        retry_count += 1
