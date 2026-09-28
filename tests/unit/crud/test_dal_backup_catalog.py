import datetime as dt

from starrocks_br.dal.metadata import backup_catalog
from starrocks_br.store.models import BackupPartition, JobStatus, TableInventory


def test_find_latest_full_backup_job_picks_most_recent(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    make_job(
        cluster.id,
        job_type="backup_full",
        label="sales_db_2024-01-01",
        status=JobStatus.SUCCESS.value,
        finished_at=dt.datetime(2024, 1, 1),
    )
    latest = make_job(
        cluster.id,
        job_type="backup_full",
        label="sales_db_2024-01-05",
        status=JobStatus.SUCCESS.value,
        finished_at=dt.datetime(2024, 1, 5),
    )

    found = backup_catalog.find_latest_full_backup_job(sqlite_session, cluster.id, "sales_db")

    assert found.id == latest.id


def test_find_latest_full_backup_job_returns_none_when_no_match(sqlite_session, make_cluster):
    cluster = make_cluster()

    assert backup_catalog.find_latest_full_backup_job(sqlite_session, cluster.id, "sales_db") is None


def test_find_successful_job_by_label(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    job = make_job(cluster.id, label="incr-1", status=JobStatus.SUCCESS.value)

    found = backup_catalog.find_successful_job_by_label(sqlite_session, cluster.id, "incr-1")

    assert found.id == job.id


def test_list_group_table_memberships_orders_by_database_and_table(sqlite_session, make_cluster, make_group):
    cluster = make_cluster()
    group_id = make_group(cluster.id)
    sqlite_session.add(
        TableInventory(cluster_id=cluster.id, inventory_group_id=group_id, database_name="sales_db", table_name="z")
    )
    sqlite_session.add(
        TableInventory(cluster_id=cluster.id, inventory_group_id=group_id, database_name="sales_db", table_name="a")
    )
    sqlite_session.commit()

    result = backup_catalog.list_group_table_memberships(sqlite_session, cluster.id, group_id)

    assert [row.table_name for row in result] == ["a", "z"]


def test_record_partitions_inserts_rows(sqlite_session, make_cluster):
    cluster = make_cluster()
    partitions = [
        {"database": "sales_db", "table": "orders", "partition_name": "p1"},
        {"database": "sales_db", "table": "orders", "partition_name": "p2"},
    ]

    backup_catalog.record_partitions(sqlite_session, cluster.id, "backup1", partitions)

    rows = sqlite_session.query(BackupPartition).filter_by(cluster_id=cluster.id, label="backup1").all()
    assert {row.partition_name for row in rows} == {"p1", "p2"}


def test_record_partitions_no_op_when_empty(sqlite_session, make_cluster):
    cluster = make_cluster()

    backup_catalog.record_partitions(sqlite_session, cluster.id, "backup1", [])

    assert sqlite_session.query(BackupPartition).count() == 0
