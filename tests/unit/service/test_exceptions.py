"""Exact-string tests for new domain exceptions introduced by the
establish-command-layer change. Each expected string is copied verbatim
from the inline `HTTPException`/error text it replaces, so that routing
these through commands + exception translation cannot silently change a
CLI message or HTTP response body (see design.md Decisions 2-3, Risk 2)."""

from starrocks_br import exceptions


def test_snapshot_already_exists_error_message():
    err = exceptions.SnapshotAlreadyExistsError("my_snapshot")
    assert str(err) == "Snapshot 'my_snapshot' already exists in repository"
    assert err.snapshot_name == "my_snapshot"


def test_backup_execution_error_preserves_message():
    err = exceptions.BackupExecutionError("Backup 'x' failed with unexpected state: ERROR")
    assert str(err) == "Backup 'x' failed with unexpected state: ERROR"
    assert err.final_status == {}


def test_backup_execution_error_carries_final_status():
    err = exceptions.BackupExecutionError("Tracking lost", final_status={"state": "LOST"})
    assert err.final_status == {"state": "LOST"}


def test_cluster_has_active_job_error_message():
    err = exceptions.ClusterHasActiveJobError(42)
    assert str(err) == "Cluster has a job in PENDING or RUNNING state; cannot delete"
    assert err.cluster_id == 42


def test_cluster_has_enabled_schedule_error_message():
    err = exceptions.ClusterHasEnabledScheduleError(42)
    assert (
        str(err)
        == "Cluster has an enabled schedule; disable or delete it before removing the cluster"
    )
    assert err.cluster_id == 42


def test_repository_already_exists_error_message():
    err = exceptions.RepositoryAlreadyExistsError("my_repo", "my_cluster")
    assert str(err) == "Repository 'my_repo' already exists on cluster 'my_cluster'"


def test_repository_still_has_snapshots_error_message():
    err = exceptions.RepositoryStillHasSnapshotsError("my_repo")
    assert str(err) == "Repository 'my_repo' still holds snapshot data; cannot delete"


def test_invalid_cadence_error_message():
    err = exceptions.InvalidCadenceError("bad cron", "not a valid cron string")
    assert str(err) == "Invalid cadence expression 'bad cron': not a valid cron string"
