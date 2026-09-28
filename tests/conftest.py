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

import os
import tempfile

import pytest


@pytest.fixture
def config_file():
    """Create a temporary config file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""
        host: "127.0.0.1"
        port: 9030
        user: "root"
        database: "test_db"
        repository: "test_repo"
        """)
        f.flush()
        config_path = f.name

    yield config_path

    if os.path.exists(config_path):
        os.unlink(config_path)


@pytest.fixture
def invalid_yaml_file():
    """Create a temporary invalid YAML file for testing error handling."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("invalid: yaml: content")
        f.flush()
        config_path = f.name

    yield config_path

    if os.path.exists(config_path):
        os.unlink(config_path)


@pytest.fixture
def setup_password_env(monkeypatch):
    """Setup STARROCKS_PASSWORD environment variable for testing."""
    monkeypatch.setenv("STARROCKS_PASSWORD", "test_password")


@pytest.fixture
def sqlite_session():
    """An in-memory SQLite Session with all store tables created.

    Used by unit tests for ops-table modules (history, labels, concurrency,
    inventory_groups, planner, prune, restore, executor) that were converted
    from raw StarRocks SQL to SQLAlchemy ORM access in the
    move-ops-tables-to-sqlite change.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from starrocks_br.store.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    yield session
    session.close()


@pytest.fixture
def make_cluster(sqlite_session):
    """Factory fixture: persist a minimal Cluster row and return it."""
    from starrocks_br.store.models import Cluster

    def _make(name: str = "test-cluster") -> Cluster:
        cluster = Cluster(
            name=name,
            host="sr.internal",
            port=9030,
            user="backup_svc",
            password_encrypted="token",
        )
        sqlite_session.add(cluster)
        sqlite_session.commit()
        return cluster

    return _make


@pytest.fixture
def history_session_factory(sqlite_session):
    """A `session_factory` callable that always yields the shared `sqlite_session`.

    `history.append_backup_event`/`append_restore_event` are called with a real
    `session_factory` (normally `store.session.get_session_factory()`, bound to the
    app's single configured database). Tests that exercise `executor.execute_backup`/
    `restore.execute_restore`/`execute_restore_flow` against the isolated in-memory
    `sqlite_session` fixture instead patch `get_session_factory` in the module under
    test (e.g. `mocker.patch("starrocks_br.executor.get_session_factory",
    return_value=history_session_factory)`) so history writes land in that same
    session rather than the real app database.
    """

    class _NoCloseSession:
        def __init__(self, session):
            self._session = session

        def __enter__(self):
            return self._session

        def __exit__(self, *exc_info):
            return False

    return lambda: _NoCloseSession(sqlite_session)


@pytest.fixture
def make_job(sqlite_session):
    """Factory fixture: persist a minimal Job row and return it.

    Used by tests covering the append-only history log and the backup catalog
    now carried on `Job.label`/`Job.repository` (see add-job-history-log).
    """
    from starrocks_br.store.models import Job, JobStatus

    def _make(
        cluster_id: int,
        job_type: str = "backup_full",
        status: str = JobStatus.SUCCESS.value,
        label: str | None = None,
        repository: str | None = None,
        finished_at=None,
    ) -> Job:
        job = Job(
            cluster_id=cluster_id,
            job_type=job_type,
            params_json="{}",
            backend="thread",
            status=status,
            label=label,
            repository=repository,
            finished_at=finished_at,
        )
        sqlite_session.add(job)
        sqlite_session.commit()
        return job

    return _make


@pytest.fixture
def make_group(sqlite_session):
    """Factory fixture: persist a minimal InventoryGroup row and return its id."""
    from starrocks_br.store.models import InventoryGroup

    def _make(cluster_id: int, name: str = "prod") -> int:
        group = InventoryGroup(cluster_id=cluster_id, name=name)
        sqlite_session.add(group)
        sqlite_session.commit()
        return group.id

    return _make


@pytest.fixture
def mock_db(mocker):
    """Create a mocked StarRocksDB instance with context manager support."""
    mock = mocker.Mock()
    mock.__enter__ = mocker.Mock(return_value=mock)
    mock.__exit__ = mocker.Mock(return_value=False)
    mocker.patch("starrocks_br.db.StarRocksDB", return_value=mock)
    return mock


@pytest.fixture
def mock_healthy_cluster(mocker):
    """Mock a healthy cluster."""
    return mocker.patch("starrocks_br.dal.db.health.check_cluster_health", return_value=(True, "Healthy"))


@pytest.fixture
def mock_unhealthy_cluster(mocker):
    """Mock an unhealthy cluster."""
    return mocker.patch(
        "starrocks_br.dal.db.health.check_cluster_health", return_value=(False, "Cluster is unhealthy")
    )


@pytest.fixture
def mock_repo_exists(mocker):
    """Mock repository verification success."""
    return mocker.patch("starrocks_br.dal.db.repository.ensure_repository")


@pytest.fixture
def mock_validate_tables_exist(mocker):
    """Mock table validation success (no invalid tables)."""
    return mocker.patch("starrocks_br.planner.validate_tables_exist")


# --- API server fixtures -------------------------------------------------

API_TEST_KEY = "test-api-key"
API_TEST_ENCRYPTION_KEY = "l7z1jY3sVN6oJvA0Z2sBd8kQm4pXrT9uWc5eFgHhIiI="  # test-only Fernet key


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    """Configure a fresh, isolated API server environment for a single test."""
    from starrocks_br.jobs.backend import reset_registry
    from starrocks_br.store import crypto as crypto_module
    from starrocks_br.store import session as session_module

    db_path = tmp_path / "api_test.db"
    monkeypatch.setenv("STARROCKS_BR_API_KEY", API_TEST_KEY)
    monkeypatch.setenv("STARROCKS_BR_DB_ENCRYPTION_KEY", API_TEST_ENCRYPTION_KEY)
    monkeypatch.setenv("STARROCKS_BR_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("STARROCKS_BR_ENABLED_BACKENDS", "thread")
    monkeypatch.setenv("STARROCKS_BR_DEFAULT_BACKEND", "thread")

    session_module.reset_engine_cache()
    crypto_module.reset_key_cache()
    reset_registry()

    yield

    session_module.reset_engine_cache()
    crypto_module.reset_key_cache()
    reset_registry()


@pytest.fixture
def api_client(api_env):
    """A FastAPI TestClient with tables created and the bearer token pre-set."""
    from fastapi.testclient import TestClient

    from starrocks_br.api.app import create_app
    from starrocks_br.store.models import Base
    from starrocks_br.store.session import get_engine

    Base.metadata.create_all(get_engine())

    app = create_app()
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {API_TEST_KEY}"})
    return client
