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

from starrocks_br.store.models import Base, Cluster, Job, Schedule


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
    assert cluster.ops_database == "ops"
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
        group_name="g1",
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

    schedule = Schedule(
        cluster_id=cluster.id,
        job_type="backup_full",
        group_name="g1",
        cadence="0 1 * * *",
        next_run_at=datetime.datetime.now(datetime.timezone.utc),
    )
    session.add(schedule)
    session.commit()

    assert schedule.enabled is True
    assert schedule in cluster.schedules
