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

from starrocks_br import history
from starrocks_br.store.models import BackupHistory, RestoreHistory


def test_should_write_backup_history_success(sqlite_session, make_cluster):
    cluster = make_cluster()

    entry = {
        "label": "sales_db_20251015_incremental",
        "backup_type": "incremental",
        "status": "FINISHED",
        "repository": "my_repo",
        "started_at": "2025-10-15 01:00:00",
        "finished_at": "2025-10-15 01:10:00",
        "error_message": None,
    }

    history.log_backup(sqlite_session, cluster.id, entry)
    sqlite_session.commit()

    row = sqlite_session.query(BackupHistory).filter_by(cluster_id=cluster.id).one()
    assert row.label == "sales_db_20251015_incremental"
    assert row.backup_type == "incremental"
    assert row.status == "FINISHED"
    assert row.error_message is None


def test_should_store_error_message(sqlite_session, make_cluster):
    cluster = make_cluster()

    entry = {
        "label": "weekly_backup_20251019",
        "backup_type": "weekly",
        "status": "FAILED",
        "repository": "my_repo",
        "started_at": "2025-10-19 01:00:00",
        "finished_at": "2025-10-19 01:10:00",
        "error_message": "Something went wrong",
    }

    history.log_backup(sqlite_session, cluster.id, entry)
    sqlite_session.commit()

    row = sqlite_session.query(BackupHistory).filter_by(cluster_id=cluster.id).one()
    assert row.error_message == "Something went wrong"


def test_backup_history_scoped_by_cluster(sqlite_session, make_cluster):
    cluster_a = make_cluster("cluster-a")
    cluster_b = make_cluster("cluster-b")

    entry = {
        "label": "sales_db_20251015_monthly",
        "backup_type": "monthly",
        "status": "FINISHED",
        "repository": "repo",
        "started_at": "2025-10-15 01:00:00",
        "finished_at": "2025-10-15 02:00:00",
        "error_message": None,
    }

    history.log_backup(sqlite_session, cluster_a.id, entry)
    sqlite_session.commit()

    assert sqlite_session.query(BackupHistory).filter_by(cluster_id=cluster_a.id).count() == 1
    assert sqlite_session.query(BackupHistory).filter_by(cluster_id=cluster_b.id).count() == 0


def test_should_write_restore_history_success(sqlite_session, make_cluster):
    cluster = make_cluster()

    entry = {
        "job_id": "job-123",
        "backup_label": "sales_db_20251015_incremental",
        "restore_type": "table",
        "status": "FINISHED",
        "repository": "my_repo",
        "started_at": "2025-10-15 01:00:00",
        "finished_at": "2025-10-15 01:10:00",
        "error_message": None,
        "verification_checksum": "abc123",
    }

    history.log_restore(sqlite_session, cluster.id, entry)
    sqlite_session.commit()

    row = sqlite_session.query(RestoreHistory).filter_by(cluster_id=cluster.id).one()
    assert row.job_id == "job-123"
    assert row.backup_label == "sales_db_20251015_incremental"
    assert row.verification_checksum == "abc123"
