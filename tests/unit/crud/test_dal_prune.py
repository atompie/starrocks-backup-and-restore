import datetime as dt

from starrocks_br.dal.metadata import prune
from starrocks_br.store.models import BackupPartition, InventoryGroup, Job, JobStatus, TableInventory


def _add_backup_history(session, cluster_id, label, finished_at, repository="test_repo"):
    job = Job(
        cluster_id=cluster_id,
        job_type="backup_full",
        backend="thread",
        status=JobStatus.SUCCESS.value,
        label=label,
        repository=repository,
        finished_at=finished_at,
    )
    session.add(job)
    session.commit()
    return job


def test_get_successful_backups_with_group(sqlite_session, make_cluster):
    cluster = make_cluster()
    _add_backup_history(sqlite_session, cluster.id, "backup1", dt.datetime(2024, 1, 1))
    _add_backup_history(sqlite_session, cluster.id, "backup2", dt.datetime(2024, 1, 2))
    for label in ("backup1", "backup2"):
        sqlite_session.add(
            BackupPartition(
                cluster_id=cluster.id,
                key_hash=f"hash-{label}",
                label=label,
                database_name="sales_db",
                table_name="orders",
                partition_name="p1",
            )
        )
    group = InventoryGroup(cluster_id=cluster.id, name="prod_group")
    sqlite_session.add(group)
    sqlite_session.commit()
    sqlite_session.add(
        TableInventory(
            cluster_id=cluster.id, inventory_group_id=group.id, database_name="sales_db", table_name="orders"
        )
    )
    sqlite_session.commit()

    result = prune.get_successful_backups(sqlite_session, cluster.id, group.id)

    assert len(result) == 2
    assert result[0] == {
        "label": "backup1",
        "finished_at": str(dt.datetime(2024, 1, 1)),
        "repository": "test_repo",
        "inventory_group_id": group.id,
    }


def test_get_successful_backups_empty_result(sqlite_session, make_cluster):
    cluster = make_cluster()

    assert prune.get_successful_backups(sqlite_session, cluster.id, 1) == []


def test_get_successful_backups_scoped_by_cluster(sqlite_session, make_cluster):
    cluster_a = make_cluster("cluster-a")
    cluster_b = make_cluster("cluster-b")
    _add_backup_history(sqlite_session, cluster_a.id, "backup1", dt.datetime(2024, 1, 1))
    sqlite_session.add(
        BackupPartition(
            cluster_id=cluster_a.id,
            key_hash="hash-backup1",
            label="backup1",
            database_name="sales_db",
            table_name="orders",
            partition_name="p1",
        )
    )
    group_a = InventoryGroup(cluster_id=cluster_a.id, name="prod_group")
    sqlite_session.add(group_a)
    sqlite_session.commit()
    sqlite_session.add(
        TableInventory(
            cluster_id=cluster_a.id, inventory_group_id=group_a.id, database_name="sales_db", table_name="orders"
        )
    )
    sqlite_session.commit()

    assert prune.get_successful_backups(sqlite_session, cluster_b.id, group_a.id) == []


def test_cleanup_backup_history_removes_job_and_partitions(sqlite_session, make_cluster):
    cluster = make_cluster()
    _add_backup_history(sqlite_session, cluster.id, "backup1", dt.datetime(2024, 1, 1))
    sqlite_session.add(
        BackupPartition(
            cluster_id=cluster.id,
            key_hash="hash1",
            label="backup1",
            database_name="sales_db",
            table_name="orders",
            partition_name="p1",
        )
    )
    sqlite_session.commit()

    prune.cleanup_backup_history(sqlite_session, cluster.id, "backup1")

    assert sqlite_session.query(Job).filter_by(cluster_id=cluster.id, label="backup1").count() == 0
    assert sqlite_session.query(BackupPartition).filter_by(cluster_id=cluster.id, label="backup1").count() == 0


def test_cleanup_backup_history_scoped_by_cluster(sqlite_session, make_cluster):
    cluster_a = make_cluster("cluster-a")
    cluster_b = make_cluster("cluster-b")
    _add_backup_history(sqlite_session, cluster_a.id, "shared-label", dt.datetime(2024, 1, 1))
    _add_backup_history(sqlite_session, cluster_b.id, "shared-label", dt.datetime(2024, 1, 1))

    prune.cleanup_backup_history(sqlite_session, cluster_a.id, "shared-label")

    assert sqlite_session.query(Job).filter_by(cluster_id=cluster_a.id, label="shared-label").count() == 0
    assert sqlite_session.query(Job).filter_by(cluster_id=cluster_b.id, label="shared-label").count() == 1
