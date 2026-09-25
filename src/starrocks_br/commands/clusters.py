"""The single implementation of cluster management use cases that carry
business rules beyond plain CRUD (currently: the delete-guard checks).
"""

from sqlalchemy.orm import Session

from ..exceptions import ClusterHasActiveJobError, ClusterHasEnabledScheduleError
from ..store.models import Cluster, Job, JobStatus, Schedule


def delete_cluster(session: Session, cluster: Cluster) -> None:
    """Delete `cluster`, refusing when it has an active job or an enabled schedule."""
    active_job = (
        session.query(Job)
        .filter(
            Job.cluster_id == cluster.id,
            Job.status.in_([JobStatus.PENDING.value, JobStatus.RUNNING.value]),
        )
        .first()
    )
    if active_job is not None:
        raise ClusterHasActiveJobError(cluster.id)

    enabled_schedule = (
        session.query(Schedule)
        .filter(Schedule.cluster_id == cluster.id, Schedule.enabled.is_(True))
        .first()
    )
    if enabled_schedule is not None:
        raise ClusterHasEnabledScheduleError(cluster.id)

    session.delete(cluster)
