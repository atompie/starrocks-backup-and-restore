import datetime as dt

from starrocks_br.dal.metadata import restore_catalog
from starrocks_br.store.models import BackupPartition, JobStatus, TableInventory


def test_find_successful_job_returns_matching_job(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    job = make_job(cluster.id, label="full-1", status=JobStatus.SUCCESS.value)

    found = restore_catalog.find_successful_job(sqlite_session, cluster.id, "full-1")

    assert found.id == job.id


def test_find_successful_job_excludes_failed(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    make_job(cluster.id, label="full-1", status=JobStatus.FAILED.value)

    assert restore_catalog.find_successful_job(sqlite_session, cluster.id, "full-1") is None


def test_find_latest_full_backup_before_picks_most_recent(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    make_job(
        cluster.id,
        job_type="backup_full",
        label="sales_db_2024-01-01",
        status=JobStatus.SUCCESS.value,
        finished_at=dt.datetime(2024, 1, 1),
    )
    later = make_job(
        cluster.id,
        job_type="backup_full",
        label="sales_db_2024-01-05",
        status=JobStatus.SUCCESS.value,
        finished_at=dt.datetime(2024, 1, 5),
    )

    found = restore_catalog.find_latest_full_backup_before(
        sqlite_session, cluster.id, "sales_db", dt.datetime(2024, 1, 10)
    )

    assert found.id == later.id


def test_list_partitions_for_label(sqlite_session, make_cluster):
    cluster = make_cluster()
    sqlite_session.add(
        BackupPartition(
            cluster_id=cluster.id,
            key_hash="h1",
            label="backup1",
            database_name="sales_db",
            table_name="orders",
            partition_name="p1",
        )
    )
    sqlite_session.commit()

    result = restore_catalog.list_partitions_for_label(sqlite_session, cluster.id, "backup1")

    assert result == [("sales_db", "orders")]


def test_list_group_table_memberships(sqlite_session, make_cluster, make_group):
    cluster = make_cluster()
    group_id = make_group(cluster.id)
    sqlite_session.add(
        TableInventory(
            cluster_id=cluster.id, inventory_group_id=group_id, database_name="sales_db", table_name="*"
        )
    )
    sqlite_session.commit()

    result = restore_catalog.list_group_table_memberships(sqlite_session, cluster.id, group_id)

    assert result == [("sales_db", "*")]


def test_list_partition_names(sqlite_session, make_cluster):
    cluster = make_cluster()
    sqlite_session.add(
        BackupPartition(
            cluster_id=cluster.id,
            key_hash="h1",
            label="backup1",
            database_name="sales_db",
            table_name="orders",
            partition_name="p2",
        )
    )
    sqlite_session.add(
        BackupPartition(
            cluster_id=cluster.id,
            key_hash="h2",
            label="backup1",
            database_name="sales_db",
            table_name="orders",
            partition_name="p1",
        )
    )
    sqlite_session.commit()

    result = restore_catalog.list_partition_names(sqlite_session, cluster.id, "backup1", "sales_db", "orders")

    assert result == ["p1", "p2"]
