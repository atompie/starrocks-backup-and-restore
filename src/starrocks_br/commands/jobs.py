"""The single implementation of job submission.

Shared by direct API job-submission routes and `commands.schedules.run_due_schedules`,
per specs/api-scheduling "submits a job through the same job-submission path
used by direct API job submission". Raises `jobs.backend.UnknownBackendError`
(already a plain `ValueError` subclass, no HTTP concept) instead of
`HTTPException` on an unresolvable backend - the caller translates it.
"""

import json

from sqlalchemy.orm import Session

from ..jobs.backend import get_registry
from ..store.models import Cluster, Job


def submit_job(
    db: Session,
    cluster: Cluster,
    job_type: str,
    params: dict,
    requested_backend: str | None,
) -> Job:
    """Create a Job row and enqueue it on the resolved backend."""
    registry = get_registry()
    backend_name = registry.resolve(requested_backend, cluster.default_backend)

    job = Job(
        cluster_id=cluster.id,
        job_type=job_type,
        params_json=json.dumps(params),
        backend=backend_name,
    )
    db.add(job)
    db.flush()
    db.refresh(job)
    # Commit now (not just flush): the enqueued backend may run the job in a
    # separate thread/connection immediately, which must see this row.
    db.commit()

    registry.get(backend_name).enqueue(job.id)
    return job
