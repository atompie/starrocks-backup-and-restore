"""In-process ThreadPoolExecutor-backed JobBackend.

Ships as the default (and today, only) backend. A future out-of-process
backend (Kafka/Redis-backed worker) would implement the same JobBackend
Protocol but hand `job_id` off to a message broker instead of a thread pool,
and a separate worker process would call the same JOB_HANDLERS functions -
see design.md Decision 3.
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

from .. import logger
from ..dal.metadata import jobs as jobs_dal
from ..runtime_config import get_job_heartbeat_seconds
from ..store.models import JobStatus
from ..store.session import session_scope
from .handlers import JOB_HANDLERS


def _make_progress_callback(job_id: int):
    def _on_progress(update: dict) -> None:
        with session_scope() as session:
            jobs_dal.mark_progress(session, job_id, update.get("state"), update.get("progress_pct"))

    return _on_progress


def _heartbeat_loop(job_id: int, stop: threading.Event, interval: float) -> None:
    """Refresh `Job.heartbeat_at` every `interval` seconds until `stop` is set.

    Independent of the handler's progress callbacks (non-polling handlers such as
    `schedule_cleanup` never call them), and uses its own short session per write so no
    transaction is held open between beats. A failed write is logged and retried on the next
    beat: `STARROCKS_BR_JOB_STALE_SECONDS` is at least 3x the interval precisely so an isolated
    failure never makes a live job look dead.
    """
    while not stop.wait(interval):
        try:
            with session_scope() as session:
                jobs_dal.touch_heartbeat(session, job_id)
        except Exception as e:
            logger.error(f"Failed to write heartbeat for job {job_id}: {e}")


def _run_job(job_id: int) -> None:
    with session_scope() as session:
        job = jobs_dal.get(session, job_id)
        # The dispatcher already admitted the job (`PENDING` -> `RUNNING`); anything else was
        # not handed to this worker.
        if job is None or job.status != JobStatus.RUNNING.value:
            return
        cluster = job.cluster
        job_type = job.job_type
        params = json.loads(job.params_json or "{}")
        session.expunge(cluster)
        session.expunge(job)

    handler = JOB_HANDLERS[job_type]
    on_progress = _make_progress_callback(job_id)

    stop_heartbeat = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat_loop,
        args=(job_id, stop_heartbeat, get_job_heartbeat_seconds()),
        name=f"starrocks-br-heartbeat-{job_id}",
        daemon=True,
    )
    heartbeat.start()

    failure: Exception | None = None
    result: dict = {}
    try:
        result = handler(cluster, params, job_id, on_progress)
    except Exception as e:
        failure = e
    finally:
        stop_heartbeat.set()
        heartbeat.join()

    with session_scope() as session:
        if failure is not None:
            jobs_dal.mark_failed(session, job_id, str(failure))
        else:
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
