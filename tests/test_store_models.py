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

import datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from starrocks_br.store.models import (
    BackupHistory,
    BackupPartition,
    Base,
    Cluster,
    InventoryGroup,
    Job,
    RestoreHistory,
    RunStatus,
    Schedule,
    TableInventory,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    yield session
    session.close()


def _make_cluster(name="prod-eu") -> Cluster:
    return Cluster(
        name=name,
        host="sr.internal",
        port=9030,
        user="backup_svc",
        password_encrypted="token",
        database="mydb",
        repository="s3_repo",
    )


def test_create_all_tables_in_memory(session):
    """Section 2.1: all three tables can be created against in-memory SQLite."""
    cluster = _make_cluster()
    session.add(cluster)
    session.commit()

    assert cluster.id is not None
    assert cluster.default_backend == "thread"


def test_cluster_name_must_be_unique(session):
    session.add(_make_cluster("dup"))
    session.commit()

    session.add(_make_cluster("dup"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_job_requires_existing_cluster(session):
    job = Job(cluster_id=999, job_type="backup_full", backend="thread")
    session.add(job)
    with pytest.raises(IntegrityError):
        session.commit()


def test_job_links_to_cluster(session):
    cluster = _make_cluster()
    session.add(cluster)
    session.commit()

    job = Job(cluster_id=cluster.id, job_type="backup_full", backend="thread")
    session.add(job)
    session.commit()

    assert job.status == "PENDING"
    assert job in cluster.jobs


def test_schedule_requires_existing_cluster(session):
    schedule = Schedule(
        cluster_id=999,
        job_type="backup_full",
        inventory_group_id=1,
        cadence="0 1 * * *",
        next_run_at=datetime.datetime.now(datetime.timezone.utc),
    )
    session.add(schedule)
    with pytest.raises(IntegrityError):
        session.commit()


def test_schedule_links_to_cluster(session):
    cluster = _make_cluster()
    session.add(cluster)
    session.commit()
    group = InventoryGroup(cluster_id=cluster.id, name="g1")
    session.add(group)
    session.commit()

    schedule = Schedule(
        cluster_id=cluster.id,
        job_type="backup_full",
        inventory_group_id=group.id,
        cadence="0 1 * * *",
        next_run_at=datetime.datetime.now(datetime.timezone.utc),
    )
    session.add(schedule)
    session.commit()

    assert schedule.enabled is True
    assert schedule in cluster.schedules


def test_table_inventory_uniqueness_per_cluster(session):
    cluster = _make_cluster()
    session.add(cluster)
    session.commit()
    group = InventoryGroup(cluster_id=cluster.id, name="g1")
    session.add(group)
    session.commit()

    session.add(
        TableInventory(cluster_id=cluster.id, inventory_group_id=group.id, database_name="db1", table_name="t1")
    )
    session.commit()

    session.add(
        TableInventory(cluster_id=cluster.id, inventory_group_id=group.id, database_name="db1", table_name="t1")
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_backup_history_uniqueness_per_cluster(session):
    cluster = _make_cluster()
    session.add(cluster)
    session.commit()

    now = datetime.datetime.now(datetime.timezone.utc)
    session.add(
        BackupHistory(
            cluster_id=cluster.id, label="lbl1", backup_type="full", status="FINISHED",
            repository="repo", started_at=now,
        )
    )
    session.commit()

    session.add(
        BackupHistory(
            cluster_id=cluster.id, label="lbl1", backup_type="full", status="FINISHED",
            repository="repo", started_at=now,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_restore_history_uniqueness_per_cluster(session):
    cluster = _make_cluster()
    session.add(cluster)
    session.commit()

    now = datetime.datetime.now(datetime.timezone.utc)
    session.add(
        RestoreHistory(
            cluster_id=cluster.id, job_id="job1", backup_label="lbl1", restore_type="table",
            status="FINISHED", repository="repo", started_at=now,
        )
    )
    session.commit()

    session.add(
        RestoreHistory(
            cluster_id=cluster.id, job_id="job1", backup_label="lbl1", restore_type="table",
            status="FINISHED", repository="repo", started_at=now,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_run_status_uniqueness_per_cluster(session):
    cluster = _make_cluster()
    session.add(cluster)
    session.commit()

    session.add(RunStatus(cluster_id=cluster.id, scope="backup", label="lbl1"))
    session.commit()

    session.add(RunStatus(cluster_id=cluster.id, scope="backup", label="lbl1"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_backup_partition_uniqueness_per_cluster(session):
    cluster = _make_cluster()
    session.add(cluster)
    session.commit()

    session.add(
        BackupPartition(
            cluster_id=cluster.id, key_hash="hash1", label="lbl1", database_name="db1",
            table_name="t1", partition_name="p1",
        )
    )
    session.commit()

    session.add(
        BackupPartition(
            cluster_id=cluster.id, key_hash="hash1", label="lbl1", database_name="db1",
            table_name="t1", partition_name="p1",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_ops_tables_are_not_unique_across_different_clusters(session):
    """The same (group/label/scope+label/key_hash) is fine on two different clusters."""
    cluster_a = _make_cluster("cluster-a")
    cluster_b = _make_cluster("cluster-b")
    session.add_all([cluster_a, cluster_b])
    session.commit()
    group_a = InventoryGroup(cluster_id=cluster_a.id, name="g1")
    group_b = InventoryGroup(cluster_id=cluster_b.id, name="g1")
    session.add_all([group_a, group_b])
    session.commit()

    session.add(
        TableInventory(cluster_id=cluster_a.id, inventory_group_id=group_a.id, database_name="db1", table_name="t1")
    )
    session.add(
        TableInventory(cluster_id=cluster_b.id, inventory_group_id=group_b.id, database_name="db1", table_name="t1")
    )
    session.commit()  # must not raise


def test_deleting_cluster_cascades_to_all_ops_tables(session):
    """Validates PRAGMA foreign_keys=ON wiring in store/session.py, not just the model
    declarations - if this fails, suspect the pragma event hook first. Also covers the
    two-level cascade: cluster -> inventory_groups -> table_inventory."""
    cluster = _make_cluster()
    session.add(cluster)
    session.commit()
    cluster_id = cluster.id
    now = datetime.datetime.now(datetime.timezone.utc)
    group = InventoryGroup(cluster_id=cluster_id, name="g1")
    session.add(group)
    session.commit()
    group_id = group.id

    session.add(
        TableInventory(cluster_id=cluster_id, inventory_group_id=group_id, database_name="db1", table_name="t1")
    )
    session.add(
        BackupHistory(
            cluster_id=cluster_id, label="lbl1", backup_type="full", status="FINISHED",
            repository="repo", started_at=now,
        )
    )
    session.add(
        RestoreHistory(
            cluster_id=cluster_id, job_id="job1", backup_label="lbl1", restore_type="table",
            status="FINISHED", repository="repo", started_at=now,
        )
    )
    session.add(RunStatus(cluster_id=cluster_id, scope="backup", label="lbl1"))
    session.add(
        BackupPartition(
            cluster_id=cluster_id, key_hash="hash1", label="lbl1", database_name="db1",
            table_name="t1", partition_name="p1",
        )
    )
    session.commit()

    session.delete(cluster)
    session.commit()

    assert session.query(TableInventory).filter_by(cluster_id=cluster_id).count() == 0
    assert session.query(BackupHistory).filter_by(cluster_id=cluster_id).count() == 0
    assert session.query(RestoreHistory).filter_by(cluster_id=cluster_id).count() == 0
    assert session.query(RunStatus).filter_by(cluster_id=cluster_id).count() == 0
    assert session.query(BackupPartition).filter_by(cluster_id=cluster_id).count() == 0
    assert session.query(InventoryGroup).filter_by(cluster_id=cluster_id).count() == 0
