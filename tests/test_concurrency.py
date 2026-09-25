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

from starrocks_br import concurrency, exceptions
from starrocks_br.store.models import RunStatus


def _add_run_status(session, cluster_id, scope, label, state="ACTIVE"):
    session.add(RunStatus(cluster_id=cluster_id, scope=scope, label=label, state=state))
    session.commit()


def test_should_reserve_job_slot_when_no_active_conflict(sqlite_session, make_cluster, mocker):
    cluster = make_cluster()
    db = mocker.Mock()

    concurrency.reserve_job_slot(db, sqlite_session, cluster.id, "backup", "db_20251015_incremental")

    row = sqlite_session.query(RunStatus).filter_by(cluster_id=cluster.id).one()
    assert row.scope == "backup"
    assert row.label == "db_20251015_incremental"
    assert row.state == "ACTIVE"
    db.query.assert_not_called()


def test_should_raise_when_active_conflict_exists(sqlite_session, make_cluster, mocker):
    cluster = make_cluster()
    db = mocker.Mock()
    _add_run_status(sqlite_session, cluster.id, "backup", "db_20251015_incremental")
    db.query.side_effect = [
        [("some_db",)],
        [("some_db", "db_20251015_incremental", "2024-01-01", "UPLOADING")],
    ]  # SHOW DATABASES + SHOW BACKUP show the job is genuinely still running

    try:
        concurrency.reserve_job_slot(db, sqlite_session, cluster.id, "backup", "db_20251015_incremental")
        raise AssertionError("expected conflict")
    except exceptions.ConcurrencyConflictError as e:
        assert e.scope == "backup"
        assert e.active_labels == ["db_20251015_incremental"]
        error_msg = str(e)
        assert "Concurrency conflict" in error_msg
        assert "Another 'backup' job is already active" in error_msg
        assert "backup:db_20251015_incremental" in error_msg

    assert sqlite_session.query(RunStatus).filter_by(cluster_id=cluster.id).count() == 1


def test_should_update_state_when_completing_job_slot(sqlite_session, make_cluster):
    cluster = make_cluster()
    _add_run_status(sqlite_session, cluster.id, "backup", "db_20251015_incremental")

    concurrency.complete_job_slot(sqlite_session, cluster.id, "backup", "db_20251015_incremental", "FINISHED")

    row = sqlite_session.query(RunStatus).filter_by(cluster_id=cluster.id).one()
    assert row.state == "FINISHED"
    assert row.finished_at is not None


def test_complete_job_slot_is_a_noop_when_row_missing(sqlite_session, make_cluster):
    cluster = make_cluster()

    concurrency.complete_job_slot(sqlite_session, cluster.id, "backup", "missing", "FINISHED")

    assert sqlite_session.query(RunStatus).filter_by(cluster_id=cluster.id).count() == 0


def test_should_not_conflict_on_different_scope(sqlite_session, make_cluster, mocker):
    cluster = make_cluster()
    db = mocker.Mock()
    _add_run_status(sqlite_session, cluster.id, "restore", "some")

    concurrency.reserve_job_slot(db, sqlite_session, cluster.id, "backup", "L1")

    assert sqlite_session.query(RunStatus).filter_by(cluster_id=cluster.id, scope="backup").count() == 1
    db.query.assert_not_called()


def test_should_cleanup_stale_backup_job_and_proceed(sqlite_session, make_cluster, mocker):
    """Test that stale backup jobs are automatically cleaned up and new job can proceed."""
    cluster = make_cluster()
    _add_run_status(sqlite_session, cluster.id, "backup", "stale_backup_label")
    db = mocker.Mock()
    db.query.side_effect = [
        [("test_db",)],
        [("test_db", "stale_backup_label", "2024-01-01", "FINISHED")],
    ]

    concurrency.reserve_job_slot(db, sqlite_session, cluster.id, "backup", "new_backup_label")

    stale = sqlite_session.query(RunStatus).filter_by(cluster_id=cluster.id, label="stale_backup_label").one()
    assert stale.state == "CANCELLED"
    new = sqlite_session.query(RunStatus).filter_by(cluster_id=cluster.id, label="new_backup_label").one()
    assert new.state == "ACTIVE"


def test_should_raise_conflict_when_backup_job_is_still_active(sqlite_session, make_cluster, mocker):
    """Test that real conflicts are still detected when backup job is actually running."""
    cluster = make_cluster()
    _add_run_status(sqlite_session, cluster.id, "backup", "active_backup_label")
    db = mocker.Mock()
    db.query.side_effect = [
        [("test_db",)],
        [("test_db", "active_backup_label", "2024-01-01", "UPLOADING")],
    ]

    try:
        concurrency.reserve_job_slot(db, sqlite_session, cluster.id, "backup", "new_backup_label")
        raise AssertionError("expected conflict")
    except exceptions.ConcurrencyConflictError as e:
        assert e.scope == "backup"
        assert e.active_labels == ["active_backup_label"]
        error_msg = str(e)
        assert "Concurrency conflict" in error_msg
        assert "Another 'backup' job is already active" in error_msg
        assert "active_backup_label" in error_msg

    assert sqlite_session.query(RunStatus).filter_by(cluster_id=cluster.id, label="new_backup_label").count() == 0


def test_should_cleanup_stale_job_when_not_found_in_show_backup(sqlite_session, make_cluster, mocker):
    """Test that jobs not found in SHOW BACKUP are considered stale."""
    cluster = make_cluster()
    _add_run_status(sqlite_session, cluster.id, "backup", "missing_backup_label")
    db = mocker.Mock()
    db.query.side_effect = [
        [("test_db",)],
        [],
    ]

    concurrency.reserve_job_slot(db, sqlite_session, cluster.id, "backup", "new_backup_label")

    stale = sqlite_session.query(RunStatus).filter_by(cluster_id=cluster.id, label="missing_backup_label").one()
    assert stale.state == "CANCELLED"


def test_should_handle_multiple_databases_in_stale_check(sqlite_session, make_cluster, mocker):
    """Test that stale check works across multiple databases."""
    cluster = make_cluster()
    _add_run_status(sqlite_session, cluster.id, "backup", "stale_backup_label")
    db = mocker.Mock()
    db.query.side_effect = [
        [("db1",), ("db2",)],
        [],
        [("db2", "stale_backup_label", "2024-01-01", "FINISHED")],
    ]

    concurrency.reserve_job_slot(db, sqlite_session, cluster.id, "backup", "new_backup_label")

    assert db.query.call_count == 3
    stale = sqlite_session.query(RunStatus).filter_by(cluster_id=cluster.id, label="stale_backup_label").one()
    assert stale.state == "CANCELLED"


def test_should_skip_system_databases_in_stale_check(sqlite_session, make_cluster, mocker):
    """Test that system databases are skipped during stale check."""
    cluster = make_cluster()
    _add_run_status(sqlite_session, cluster.id, "backup", "stale_backup_label")
    db = mocker.Mock()
    db.query.side_effect = [
        [("information_schema",), ("mysql",), ("sys",), ("user_db",)],
        [("user_db", "stale_backup_label", "2024-01-01", "FINISHED")],
    ]

    concurrency.reserve_job_slot(db, sqlite_session, cluster.id, "backup", "new_backup_label")

    assert db.query.call_count == 2
    stale = sqlite_session.query(RunStatus).filter_by(cluster_id=cluster.id, label="stale_backup_label").one()
    assert stale.state == "CANCELLED"


def test_should_handle_non_backup_scope_conflicts(sqlite_session, make_cluster, mocker):
    """Test that non-backup scopes still raise conflicts (no self-healing for them)."""
    cluster = make_cluster()
    _add_run_status(sqlite_session, cluster.id, "restore", "active_restore")
    db = mocker.Mock()

    try:
        concurrency.reserve_job_slot(db, sqlite_session, cluster.id, "restore", "new_restore")
        raise AssertionError("expected conflict")
    except exceptions.ConcurrencyConflictError as e:
        assert e.scope == "restore"
        assert e.active_labels == ["active_restore"]
        error_msg = str(e)
        assert "Concurrency conflict" in error_msg
        assert "Another 'restore' job is already active" in error_msg

    db.query.assert_not_called()
    assert sqlite_session.query(RunStatus).filter_by(cluster_id=cluster.id, label="new_restore").count() == 0


def test_should_handle_exception_during_stale_check(sqlite_session, make_cluster, mocker):
    """Test that exceptions during stale check are handled gracefully."""
    cluster = make_cluster()
    _add_run_status(sqlite_session, cluster.id, "backup", "stale_backup_label")
    db = mocker.Mock()
    db.query.side_effect = Exception("Database connection error")

    try:
        concurrency.reserve_job_slot(db, sqlite_session, cluster.id, "backup", "new_backup_label")
        raise AssertionError("expected conflict")
    except exceptions.ConcurrencyConflictError as e:
        assert e.scope == "backup"
        assert e.active_labels == ["stale_backup_label"]
        error_msg = str(e)
        assert "Concurrency conflict" in error_msg
        assert "Another 'backup' job is already active" in error_msg

    assert sqlite_session.query(RunStatus).filter_by(cluster_id=cluster.id, label="new_backup_label").count() == 0


def test_run_status_scoped_by_cluster(sqlite_session, make_cluster, mocker):
    """An active job on one cluster must not conflict with another cluster's reservation."""
    cluster_a = make_cluster("cluster-a")
    cluster_b = make_cluster("cluster-b")
    _add_run_status(sqlite_session, cluster_a.id, "backup", "shared-label")
    db = mocker.Mock()

    concurrency.reserve_job_slot(db, sqlite_session, cluster_b.id, "backup", "shared-label")

    assert sqlite_session.query(RunStatus).filter_by(cluster_id=cluster_b.id).count() == 1
    db.query.assert_not_called()
