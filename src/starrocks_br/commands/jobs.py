"""The single implementation of job submission and dispatch.

Submission is shared by direct API job-submission routes and `commands.schedules.run_due_schedules`,
per specs/api-scheduling "submits a job through the same job-submission path
used by direct API job submission". Raises `jobs.backend.UnknownBackendError`
(already a plain `ValueError` subclass, no HTTP concept) instead of
`HTTPException` on an unresolvable backend - the caller translates it.

Submitting only queues a job (`PENDING`). `dispatch_pending_jobs`, called by the scheduler tick,
is the only thing that starts jobs: it admits at most one job per cluster at a time.
"""

import datetime
import json
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from .. import concurrency, logger, planner, reconcile, restore
from ..dal.metadata import clusters as clusters_dal
from ..dal.metadata import history
from ..dal.metadata import jobs as jobs_dal
from ..jobs.backend import get_registry
from ..runtime_config import get_job_stale_seconds
from ..store.models import Cluster, Job
from ..store.session import get_session_factory, session_scope
from ._shared import connect


def submit_job(
    db: Session,
    cluster: Cluster,
    job_type: str,
    params: dict,
    requested_backend: str | None,
    schedule_id: int | None = None,
    source_backup_job_id: int | None = None,
) -> Job:
    """Create a `PENDING` Job row on the resolved backend; a scheduler tick starts it later."""
    registry = get_registry()
    backend_name = registry.resolve(requested_backend, cluster.default_backend)

    return jobs_dal.create_job(
        db,
        cluster,
        job_type,
        params,
        backend_name,
        schedule_id=schedule_id,
        source_backup_job_id=source_backup_job_id,
    )


@dataclass
class DispatchSummary:
    """Job ids the dispatch pass acted on: `admitted` were started, `failed` could not be enqueued."""

    admitted: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)


def _admit_next_job(cluster_id: int, now: datetime.datetime) -> tuple[int, str] | None:
    """Claim the next job for `cluster_id` if its lane is free; returns `(job_id, backend)`.

    One short session: metadata only, StarRocks is never contacted. The claim commits before the
    job is handed to its backend, so the worker (and the next dispatcher) see it as `RUNNING`.
    """
    with session_scope() as session:
        if jobs_dal.cluster_has_running_job(session, cluster_id):
            return None
        job = jobs_dal.next_pending_job(session, cluster_id)
        if job is None or not jobs_dal.claim_pending_job(session, job.id, now):
            return None
        return job.id, job.backend


def dispatch_pending_jobs(now: datetime.datetime | None = None) -> DispatchSummary:
    """Admit at most one `PENDING` job per cluster whose lane is free, and enqueue it.

    A cluster with a `RUNNING` job admits nothing, so jobs on one cluster run one after another
    (restore, then backup, then other work; oldest first), while different clusters proceed
    independently. A job whose backend cannot take it is marked `FAILED`; one cluster's error
    never stops the pass.
    """
    now = now or _utcnow()
    summary = DispatchSummary()

    with session_scope() as session:
        cluster_ids = jobs_dal.clusters_with_pending_jobs(session)

    for cluster_id in cluster_ids:
        try:
            admitted = _admit_next_job(cluster_id, now)
        except Exception as e:
            logger.error(f"Failed to admit a job on cluster {cluster_id}: {e}")
            continue
        if admitted is None:
            continue

        job_id, backend_name = admitted
        try:
            get_registry().get(backend_name).enqueue(job_id)
        except Exception as e:
            logger.error(f"Failed to enqueue job {job_id} on backend '{backend_name}': {e}")
            with session_scope() as session:
                jobs_dal.mark_failed(session, job_id, f"Failed to start on backend '{backend_name}': {e}")
            summary.failed.append(job_id)
            continue
        summary.admitted.append(job_id)
        logger.info(f"Admitted job {job_id} on cluster {cluster_id}")

    return summary


def list_jobs(
    db: Session,
    cluster_id: int,
    job_type: str | list[str] | None = None,
    status: str | None = None,
    job_id: int | None = None,
    group_id: int | None = None,
    schedule_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Job]:
    return jobs_dal.list_jobs(
        db,
        cluster_id,
        job_type=job_type,
        status=status,
        job_id=job_id,
        group_id=group_id,
        schedule_id=schedule_id,
        limit=limit,
        offset=offset,
    )


def get_job(db: Session, job_id: int) -> Job | None:
    return jobs_dal.get(db, job_id)


def get_job_history(db: Session, job_type: str, job_id: int) -> list:
    return jobs_dal.list_history_for_job(db, job_type, job_id)


def get_job_references(db: Session, job_id: int) -> list:
    return jobs_dal.list_references_for_job(db, job_id)


@dataclass
class ReconciliationSummary:
    """Job ids the reconciliation pass acted on, grouped by outcome."""

    failed: list[int] = field(default_factory=list)
    left_running: list[int] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class _StaleJob:
    """The fields reconciliation needs, copied out so no session stays open while StarRocks is queried."""

    id: int
    cluster_id: int
    job_type: str
    status: str
    label: str | None
    params: dict
    group_id: int | None
    source_backup_job_id: int | None


_BACKUP_TYPES = {"backup_full", "backup_incremental"}


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def reconcile_stale_jobs(now: datetime.datetime | None = None) -> ReconciliationSummary:
    """Recover jobs whose owning process is gone, without touching live ones.

    A job is stale when its liveness (heartbeat, else start, else creation time) is older than
    `STARROCKS_BR_JOB_STALE_SECONDS`. Each stale job is claimed with an atomic conditional
    update first, so a concurrent tick (or a live owner refreshing its heartbeat) leaves exactly
    one actor. `PENDING` jobs are never stale: they wait for their cluster lane and are started
    only by `dispatch_pending_jobs`. Stale `RUNNING` backup/restore jobs stay
    `RUNNING` only while StarRocks still shows their operation in progress; in every other case
    they are failed - never promoted to `SUCCESS`, because the references (or remaining restore
    steps) lived only in the dead process. Any other stale `RUNNING` job is failed.

    Metadata sessions are short and closed before StarRocks is contacted. One job's failure is
    logged and does not stop the pass.
    """
    now = now or _utcnow()
    cutoff = now - datetime.timedelta(seconds=get_job_stale_seconds())
    summary = ReconciliationSummary()

    with session_scope() as session:
        candidates = [
            _StaleJob(
                id=j.id,
                cluster_id=j.cluster_id,
                job_type=j.job_type,
                status=j.status,
                label=j.label,
                params=_load_params(j.params_json),
                group_id=j.group_id,
                source_backup_job_id=j.source_backup_job_id,
            )
            for j in jobs_dal.list_stale_jobs(session, cutoff)
        ]

    for job in candidates:
        with session_scope() as session:
            claimed = jobs_dal.claim_stale_job(session, job.id, job.status, cutoff, now)
        if not claimed:
            summary.skipped.append(job.id)
            continue

        try:
            _reconcile_running_job(job, summary)
        except Exception as e:
            logger.error(f"Failed to reconcile job {job.id}: {e}")
            summary.skipped.append(job.id)

    return summary


def _load_params(params_json: str | None) -> dict:
    return json.loads(params_json or "{}")


def _reconcile_running_job(job: _StaleJob, summary: ReconciliationSummary) -> None:
    if job.job_type in _BACKUP_TYPES:
        kind = "backup"
    elif job.job_type == "restore":
        kind = "restore"
    else:
        _fail_job(job, f"Job {job.id} ({job.job_type}) lost its worker process; resubmit it", summary)
        return

    if kind == "backup" and not job.label:
        _fail_job(job, "Backup process died before StarRocks received a snapshot; no operation found", summary)
        return

    with session_scope() as session:
        cluster = clusters_dal.get(session, job.cluster_id)
        if cluster is None:
            _fail_job(job, "Cluster no longer exists", summary)
            return
        session.expunge(cluster)
        databases, labels = _operation_targets(session, job, kind)

    try:
        database = connect(cluster)
        with database:
            state = reconcile.find_operation_state(database, kind, databases, labels)
    except Exception as e:
        logger.warning(f"Cannot reach cluster {cluster.id} to reconcile job {job.id}: {e}")
        summary.left_running.append(job.id)
        return

    if state is not None and state not in reconcile.TERMINAL_STATES:
        logger.info(f"Job {job.id} is still {state} in StarRocks; leaving it RUNNING")
        summary.left_running.append(job.id)
        return

    if kind == "backup" and state == "FINISHED":
        message = (
            f"Backup process died after StarRocks finished snapshot '{job.label}' but before its "
            "references were recorded; the snapshot is not registered and cannot be restored "
            "through this system. Drop it manually if it is no longer needed."
        )
    else:
        message = f"Process died mid-{kind}; StarRocks reports {state or 'no matching operation'}"
    _fail_job(job, message, summary, final_state="CANCELLED" if state == "CANCELLED" else "FAILED")


def _operation_targets(session: Session, job: _StaleJob, kind: str) -> tuple[list[str], list[str]]:
    """Databases and labels under which StarRocks would list this job's operation."""
    if kind == "backup":
        try:
            databases = planner.resolve_group_databases(session, job.cluster_id, job.group_id)
        except Exception:
            databases = []
        return databases, [job.label]

    target_label = job.params.get("target_label", "")
    try:
        labels = restore.find_restore_pair(session, job.cluster_id, target_label)
    except Exception:
        labels = [target_label]

    databases: set[str] = set()
    if job.source_backup_job_id is not None:
        for source_id in filter(None, {job.source_backup_job_id, _baseline_of(session, job.source_backup_job_id)}):
            databases.update(ref.database_name for ref in jobs_dal.list_references_for_job(session, source_id))
    if not databases and job.params.get("database"):
        databases.add(job.params["database"])
    return sorted(databases), labels


def _baseline_of(session: Session, job_id: int) -> int | None:
    source = jobs_dal.get(session, job_id)
    return source.baseline_job_id if source is not None else None


def _fail_job(
    job: _StaleJob, message: str, summary: ReconciliationSummary, final_state: str = "FAILED"
) -> None:
    """Fail a stale job: status, history row (backup/restore/retention), and the backup's cluster slot."""
    with session_scope() as session:
        jobs_dal.mark_failed(session, job.id, message)

    try:
        if job.job_type in _BACKUP_TYPES:
            history.append_backup_event(get_session_factory(), job.id, "FAILED", message=message)
        elif job.job_type == "restore":
            history.append_restore_event(get_session_factory(), job.id, "FAILED", message=message)
        elif job.job_type == "retention":
            history.append_retention_event(get_session_factory(), job.id, "FAILED", message=message)
    except Exception:
        logger.error(f"Failed to append reconciliation history for job {job.id}")

    if job.job_type in _BACKUP_TYPES and job.label:
        with session_scope() as session:
            concurrency.complete_job_slot(
                session, job.cluster_id, scope="backup", label=job.label, final_state=final_state
            )

    logger.warning(f"Reconciled stale job {job.id}: {message}")
    summary.failed.append(job.id)
