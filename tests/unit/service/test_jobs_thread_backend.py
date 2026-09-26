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
import time

import pytest

from starrocks_br.jobs import handlers
from starrocks_br.jobs.thread_backend import ThreadBackend
from starrocks_br.store import session as session_module
from starrocks_br.store.models import Base, Cluster, Job, JobStatus


@pytest.fixture
def sqlite_store(tmp_path, monkeypatch):
    db_path = tmp_path / "jobs.db"
    monkeypatch.setenv("STARROCKS_BR_DATABASE_URL", f"sqlite:///{db_path}")
    session_module.reset_engine_cache()
    Base.metadata.create_all(session_module.get_engine())
    yield
    session_module.reset_engine_cache()


@pytest.fixture
def cluster_id(sqlite_store):
    with session_module.session_scope() as session:
        cluster = Cluster(
            name="c1",
            host="h",
            port=9030,
            user="u",
            password_encrypted="enc",
        )
        session.add(cluster)
        session.flush()
        return cluster.id


def _wait_until_terminal(job_id: int, timeout: float = 2.0) -> Job:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with session_module.session_scope() as session:
            job = session.get(Job, job_id)
            if job.status in (JobStatus.SUCCESS.value, JobStatus.FAILED.value):
                session.expunge(job)
                return job
        time.sleep(0.02)
    raise TimeoutError(f"job {job_id} did not reach a terminal state in {timeout}s")


def test_thread_backend_success_path(cluster_id, monkeypatch):
    def fake_handler(cluster, params, on_progress=None):
        if on_progress:
            on_progress({"state": "UPLOADING", "progress_pct": 50})
        return {"ok": True}

    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", fake_handler)

    with session_module.session_scope() as session:
        job = Job(cluster_id=cluster_id, job_type="backup_full", backend="thread", params_json="{}")
        session.add(job)
        session.flush()
        job_id = job.id
        assert job.status == JobStatus.PENDING.value

    backend = ThreadBackend(max_workers=2)
    try:
        backend.enqueue(job_id)
        final = _wait_until_terminal(job_id)
    finally:
        backend.shutdown(wait=True)

    assert final.status == JobStatus.SUCCESS.value
    assert final.started_at is not None
    assert final.finished_at is not None
    assert json.loads(final.result_json) == {"ok": True}


def test_thread_backend_failure_path(cluster_id, monkeypatch):
    def failing_handler(cluster, params, on_progress=None):
        raise RuntimeError("boom")

    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", failing_handler)

    with session_module.session_scope() as session:
        job = Job(cluster_id=cluster_id, job_type="backup_full", backend="thread", params_json="{}")
        session.add(job)
        session.flush()
        job_id = job.id

    backend = ThreadBackend(max_workers=2)
    try:
        backend.enqueue(job_id)
        final = _wait_until_terminal(job_id)
    finally:
        backend.shutdown(wait=True)

    assert final.status == JobStatus.FAILED.value
    assert final.error_message == "boom"


def test_progress_callback_updates_job_row_mid_run(cluster_id, monkeypatch):
    seen_mid_run = {}

    def handler_with_progress(cluster, params, on_progress=None):
        on_progress({"state": "UPLOADING", "progress_pct": 77})
        with session_module.session_scope() as session:
            job = session.query(Job).filter_by(cluster_id=cluster.id).first()
            seen_mid_run["progress_pct"] = job.progress_pct
            seen_mid_run["state_detail"] = job.state_detail
        return {}

    monkeypatch.setitem(handlers.JOB_HANDLERS, "backup_full", handler_with_progress)

    with session_module.session_scope() as session:
        job = Job(cluster_id=cluster_id, job_type="backup_full", backend="thread", params_json="{}")
        session.add(job)
        session.flush()
        job_id = job.id

    backend = ThreadBackend(max_workers=2)
    try:
        backend.enqueue(job_id)
        _wait_until_terminal(job_id)
    finally:
        backend.shutdown(wait=True)

    assert seen_mid_run["progress_pct"] == 77
    assert seen_mid_run["state_detail"] == "UPLOADING"
