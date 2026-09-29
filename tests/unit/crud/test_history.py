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

from starrocks_br.dal.metadata import history
from starrocks_br.dal.metadata import jobs as jobs_dal
from starrocks_br.store.models import BackupHistory, Job, RestoreHistory, RetentionHistory


def test_should_write_backup_history_success(sqlite_session, make_cluster, make_job, history_session_factory):
    cluster = make_cluster()
    job = make_job(cluster.id)

    history.append_backup_event(history_session_factory, job.id, "SNAPSHOTING")

    row = sqlite_session.query(BackupHistory).filter_by(job_id=job.id).one()
    assert row.status == "SNAPSHOTING"
    assert row.message is None


def test_should_store_message(sqlite_session, make_cluster, make_job, history_session_factory):
    cluster = make_cluster()
    job = make_job(cluster.id)

    history.append_backup_event(history_session_factory, job.id, "FAILED", message="CANCELLED")

    row = sqlite_session.query(BackupHistory).filter_by(job_id=job.id).one()
    assert row.message == "CANCELLED"


def test_backup_history_scoped_by_job(sqlite_session, make_cluster, make_job, history_session_factory):
    cluster = make_cluster()
    job_a = make_job(cluster.id)
    job_b = make_job(cluster.id)

    history.append_backup_event(history_session_factory, job_a.id, "SNAPSHOTING")

    assert sqlite_session.query(BackupHistory).filter_by(job_id=job_a.id).count() == 1
    assert sqlite_session.query(BackupHistory).filter_by(job_id=job_b.id).count() == 0


def test_should_not_duplicate_consecutive_identical_backup_status(
    sqlite_session, make_cluster, make_job, history_session_factory
):
    cluster = make_cluster()
    job = make_job(cluster.id)

    history.append_backup_event(history_session_factory, job.id, "UPLOADING")
    history.append_backup_event(history_session_factory, job.id, "UPLOADING")
    history.append_backup_event(history_session_factory, job.id, "UPLOADING")

    assert sqlite_session.query(BackupHistory).filter_by(job_id=job.id).count() == 1


def test_terminal_backup_status_is_always_appended(
    sqlite_session, make_cluster, make_job, history_session_factory
):
    cluster = make_cluster()
    job = make_job(cluster.id)

    history.append_backup_event(history_session_factory, job.id, "UPLOADING")
    history.append_backup_event(history_session_factory, job.id, "SUCCESS")

    rows = sqlite_session.query(BackupHistory).filter_by(job_id=job.id).order_by(BackupHistory.id).all()
    assert [row.status for row in rows] == ["UPLOADING", "SUCCESS"]


def test_should_write_restore_history_success(sqlite_session, make_cluster, make_job, history_session_factory):
    cluster = make_cluster()
    job = make_job(cluster.id, job_type="restore")

    history.append_restore_event(history_session_factory, job.id, "PENDING")

    row = sqlite_session.query(RestoreHistory).filter_by(job_id=job.id).one()
    assert row.status == "PENDING"


def test_should_not_duplicate_consecutive_identical_restore_status(
    sqlite_session, make_cluster, make_job, history_session_factory
):
    cluster = make_cluster()
    job = make_job(cluster.id, job_type="restore")

    history.append_restore_event(history_session_factory, job.id, "DOWNLOADING")
    history.append_restore_event(history_session_factory, job.id, "DOWNLOADING")

    assert sqlite_session.query(RestoreHistory).filter_by(job_id=job.id).count() == 1


def test_cluster_scoped_history_lookup_via_job_join(
    sqlite_session, make_cluster, make_job, history_session_factory
):
    """`cluster_id` isn't duplicated onto history rows (design.md); a cluster-scoped
    history query joins through `Job.cluster_id` instead."""
    cluster_a = make_cluster("cluster-a")
    cluster_b = make_cluster("cluster-b")
    job_a = make_job(cluster_a.id)
    job_b = make_job(cluster_b.id)

    history.append_backup_event(history_session_factory, job_a.id, "SNAPSHOTING")
    history.append_backup_event(history_session_factory, job_b.id, "SNAPSHOTING")

    rows = (
        sqlite_session.query(BackupHistory)
        .join(Job, Job.id == BackupHistory.job_id)
        .filter(Job.cluster_id == cluster_a.id)
        .all()
    )

    assert [row.job_id for row in rows] == [job_a.id]


def test_history_rows_are_never_updated_or_deleted(
    sqlite_session, make_cluster, make_job, history_session_factory
):
    """Append-only invariant: no code path in `history.py` updates or deletes a row."""
    cluster = make_cluster()
    job = make_job(cluster.id)

    history.append_backup_event(history_session_factory, job.id, "SNAPSHOTING")
    history.append_backup_event(history_session_factory, job.id, "UPLOADING")
    history.append_backup_event(history_session_factory, job.id, "SUCCESS")

    rows = sqlite_session.query(BackupHistory).filter_by(job_id=job.id).order_by(BackupHistory.id).all()
    assert [row.status for row in rows] == ["SNAPSHOTING", "UPLOADING", "SUCCESS"]

    import inspect

    source = inspect.getsource(history)
    assert ".delete(" not in source
    assert ".update(" not in source


def test_retention_events_are_appended_and_listed_via_the_job_history(
    sqlite_session, make_cluster, make_job, history_session_factory
):
    cluster = make_cluster()
    job = make_job(cluster.id, job_type="retention")

    history.append_retention_event(history_session_factory, job.id, "RETENTION_STARTED")
    history.append_retention_event(history_session_factory, job.id, "RETENTION_STARTED")
    history.append_retention_event(history_session_factory, job.id, "SNAPSHOT_DROPPED", details={"job_id": 1})
    history.append_retention_event(history_session_factory, job.id, "SNAPSHOT_DROPPED", details={"job_id": 2})
    history.append_retention_event(history_session_factory, job.id, "RETENTION_FINISHED")

    assert sqlite_session.query(RetentionHistory).filter_by(job_id=job.id).count() == 4
    rows = jobs_dal.list_history_for_job(sqlite_session, "retention", job.id)
    assert [row.status for row in rows] == [
        "RETENTION_STARTED",
        "SNAPSHOT_DROPPED",
        "SNAPSHOT_DROPPED",
        "RETENTION_FINISHED",
    ]
