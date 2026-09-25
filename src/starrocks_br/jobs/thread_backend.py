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

"""In-process ThreadPoolExecutor-backed JobBackend.

Ships as the default (and today, only) backend. A future out-of-process
backend (Kafka/Redis-backed worker) would implement the same JobBackend
Protocol but hand `job_id` off to a message broker instead of a thread pool,
and a separate worker process would call the same JOB_HANDLERS functions -
see design.md Decision 3.
"""

import datetime
import json
from concurrent.futures import ThreadPoolExecutor

from ..store.models import Job, JobStatus
from ..store.session import session_scope
from .handlers import JOB_HANDLERS


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _make_progress_callback(job_id: int):
    def _on_progress(update: dict) -> None:
        with session_scope() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            job.state_detail = update.get("state")
            progress_pct = update.get("progress_pct")
            if progress_pct is not None:
                job.progress_pct = progress_pct

    return _on_progress


def _run_job(job_id: int) -> None:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        cluster = job.cluster
        job_type = job.job_type
        params = json.loads(job.params_json or "{}")
        job.status = JobStatus.RUNNING.value
        job.started_at = _utcnow()
        session.flush()
        session.expunge(cluster)
        session.expunge(job)

    handler = JOB_HANDLERS[job_type]
    on_progress = _make_progress_callback(job_id)

    try:
        result = handler(cluster, params, on_progress)
    except Exception as e:
        with session_scope() as session:
            job = session.get(Job, job_id)
            if job is not None:
                job.status = JobStatus.FAILED.value
                job.error_message = str(e)
                job.finished_at = _utcnow()
        return

    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is not None:
            job.status = JobStatus.SUCCESS.value
            job.result_json = json.dumps(result, default=str)
            job.finished_at = _utcnow()


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
