import datetime as dt

from starrocks_br.dal.metadata import restore_catalog
from starrocks_br.store.models import BackupReference, JobStatus, TableInventory


def test_find_successful_job_returns_matching_job(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    job = make_job(cluster.id, label="full-1", status=JobStatus.SUCCESS.value)

    found = restore_catalog.find_successful_job(sqlite_session, cluster.id, "full-1")

    assert found.id == job.id


def test_find_successful_job_excludes_failed(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    make_job(cluster.id, label="full-1", status=JobStatus.FAILED.value)

    assert restore_catalog.find_successful_job(sqlite_session, cluster.id, "full-1") is None


def _add_reference(session, job, database_name, table_name="orders", partition_name="p1", repository="repo1"):
    session.add(
        BackupReference(
            job_id=job.id,
            repository=repository,
            snapshot_label=job.label,
            snapshot_timestamp=job.finished_at,
            database_name=database_name,
            table_name=table_name,
            partition_name=partition_name,
        )
    )
    session.commit()


def test_find_latest_full_backup_before_picks_most_recent(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    older = make_job(
        cluster.id,
        job_type="backup_full",
        label="sales_db_2024-01-01",
        status=JobStatus.SUCCESS.value,
        finished_at=dt.datetime(2024, 1, 1),
    )
    _add_reference(sqlite_session, older, "sales_db")
    later = make_job(
        cluster.id,
        job_type="backup_full",
        label="sales_db_2024-01-05",
        status=JobStatus.SUCCESS.value,
        finished_at=dt.datetime(2024, 1, 5),
    )
    _add_reference(sqlite_session, later, "sales_db")

    found = restore_catalog.find_latest_full_backup_before(
        sqlite_session, cluster.id, "sales_db", dt.datetime(2024, 1, 10)
    )

    assert found.id == later.id


def test_find_latest_full_backup_before_resolved_by_reference_not_label(sqlite_session, make_cluster, make_job):
    """A multi-database Job's `Job.label` only reflects one database - the lookup for a
    *different* database it also covers must still find it via its `BackupReference` rows.
    """
    cluster = make_cluster()
    multi_db_job = make_job(
        cluster.id,
        job_type="backup_full",
        label="orders_db_2024-01-01",
        status=JobStatus.SUCCESS.value,
        finished_at=dt.datetime(2024, 1, 1),
    )
    _add_reference(sqlite_session, multi_db_job, "orders_db")
    _add_reference(sqlite_session, multi_db_job, "sales_db")

    found = restore_catalog.find_latest_full_backup_before(
        sqlite_session, cluster.id, "sales_db", dt.datetime(2024, 1, 10)
    )

    assert found.id == multi_db_job.id


def test_list_partitions_for_label(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    job = make_job(cluster.id, label="backup1", status=JobStatus.SUCCESS.value)
    sqlite_session.add(
        BackupReference(
            job_id=job.id,
            repository="repo1",
            snapshot_label="backup1",
            snapshot_timestamp=dt.datetime(2024, 1, 1),
            database_name="sales_db",
            table_name="orders",
            partition_name="p1",
        )
    )
    sqlite_session.commit()

    result = restore_catalog.list_partitions_for_label(sqlite_session, cluster.id, "backup1")

    assert result == [("sales_db", "orders")]


def test_list_partitions_for_label_returns_empty_for_unknown_label(sqlite_session, make_cluster):
    cluster = make_cluster()

    result = restore_catalog.list_partitions_for_label(sqlite_session, cluster.id, "missing")

    assert result == []


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


def test_list_partition_names(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    job = make_job(cluster.id, label="backup1", status=JobStatus.SUCCESS.value)
    sqlite_session.add(
        BackupReference(
            job_id=job.id,
            repository="repo1",
            snapshot_label="backup1",
            snapshot_timestamp=dt.datetime(2024, 1, 1),
            database_name="sales_db",
            table_name="orders",
            partition_name="p2",
        )
    )
    sqlite_session.add(
        BackupReference(
            job_id=job.id,
            repository="repo1",
            snapshot_label="backup1",
            snapshot_timestamp=dt.datetime(2024, 1, 1),
            database_name="sales_db",
            table_name="orders",
            partition_name="p1",
        )
    )
    sqlite_session.commit()

    result = restore_catalog.list_partition_names(sqlite_session, cluster.id, "backup1", "sales_db", "orders")

    assert result == ["p1", "p2"]


def _delete_references(session, job):
    session.query(BackupReference).filter_by(job_id=job.id).update({"deleted_at": dt.datetime(2024, 2, 1)})
    session.commit()


def test_find_latest_full_backup_before_ignores_backups_deleted_by_retention(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    kept = make_job(cluster.id, job_type="backup_full", label="a", status=JobStatus.SUCCESS.value,
                    finished_at=dt.datetime(2024, 1, 1))
    _add_reference(sqlite_session, kept, "sales_db")
    dropped = make_job(cluster.id, job_type="backup_full", label="b", status=JobStatus.SUCCESS.value,
                       finished_at=dt.datetime(2024, 1, 5))
    _add_reference(sqlite_session, dropped, "sales_db")
    _delete_references(sqlite_session, dropped)

    found = restore_catalog.find_latest_full_backup_before(
        sqlite_session, cluster.id, "sales_db", dt.datetime(2024, 1, 10)
    )

    assert found.id == kept.id


def test_list_partitions_for_label_is_empty_once_retention_deleted_the_data(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    job = make_job(cluster.id, label="a", status=JobStatus.SUCCESS.value, finished_at=dt.datetime(2024, 1, 1))
    _add_reference(sqlite_session, job, "sales_db")
    assert restore_catalog.list_partitions_for_label(sqlite_session, cluster.id, "a") == [("sales_db", "orders")]

    _delete_references(sqlite_session, job)

    assert restore_catalog.list_partitions_for_label(sqlite_session, cluster.id, "a") == []


def test_source_data_deleted_only_when_every_reference_is_deleted(sqlite_session, make_cluster, make_job):
    cluster = make_cluster()
    never_referenced = make_job(cluster.id, label="a", status=JobStatus.SUCCESS.value)
    live = make_job(cluster.id, label="b", status=JobStatus.SUCCESS.value, finished_at=dt.datetime(2024, 1, 1))
    _add_reference(sqlite_session, live, "sales_db")
    dropped = make_job(cluster.id, label="c", status=JobStatus.SUCCESS.value, finished_at=dt.datetime(2024, 1, 1))
    _add_reference(sqlite_session, dropped, "sales_db")
    _delete_references(sqlite_session, dropped)

    assert restore_catalog.source_data_deleted(sqlite_session, never_referenced.id) is False
    assert restore_catalog.source_data_deleted(sqlite_session, live.id) is False
    assert restore_catalog.source_data_deleted(sqlite_session, dropped.id) is True
