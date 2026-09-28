"""In-process ThreadPoolExecutor-backed JobBackend.

Ships as the default (and today, only) backend. A future out-of-process
backend (Kafka/Redis-backed worker) would implement the same JobBackend
Protocol but hand `job_id` off to a message broker instead of a thread pool,
and a separate worker process would call the same JOB_HANDLERS functions -
see design.md Decision 3.
"""

import json
from concurrent.futures import ThreadPoolExecutor

from ..dal.metadata import jobs as jobs_dal
from ..store.session import session_scope
from .handlers import JOB_HANDLERS


def _make_progress_callback(job_id: int):
    def _on_progress(update: dict) -> None:
        with session_scope() as session:
            jobs_dal.mark_progress(session, job_id, update.get("state"), update.get("progress_pct"))

    return _on_progress


def _run_job(job_id: int) -> None:
    with session_scope() as session:
        job = jobs_dal.get(session, job_id)
        if job is None:
            return
        cluster = job.cluster
        job_type = job.job_type
        params = json.loads(job.params_json or "{}")
        jobs_dal.mark_running(session, job_id)
        session.expunge(cluster)
        session.expunge(job)

    handler = JOB_HANDLERS[job_type]
    on_progress = _make_progress_callback(job_id)

    try:
        result = handler(cluster, params, job_id, on_progress)
    except Exception as e:
        with session_scope() as session:
            jobs_dal.mark_failed(session, job_id, str(e))
        return

    with session_scope() as session:
        jobs_dal.mark_success(session, job_id, json.dumps(result, default=str))


class ThreadBackend:
    """Runs each job in a worker thread of a shared, module-scoped thread pool."""

    name = "thread"

    def __init__(self, max_workers: int = 8):
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="starrocks-br-job"
        )

    def enqueue(self, job_id: int) -> None:
        self._executor.submit(_run_job, job_id)

    def shutdown(self, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait)
