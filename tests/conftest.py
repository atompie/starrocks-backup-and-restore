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
            database="mydb",
            repository="s3_repo",
        )
        sqlite_session.add(cluster)
        sqlite_session.commit()
        return cluster

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
def mock_resolved_cluster(mocker):
    """Mock cli.py's SQLite session/cluster lookup as already-initialized (the common case).

    Replaces the old `mock_initialized_schema` fixture from the StarRocks-side
    ops-schema era: `cli.py` no longer auto-creates anything on first use - it
    looks up a `Cluster` row in the SQLite metastore via `resolve_cluster`,
    which this fixture makes succeed without touching a real database. Tests
    using this fixture should mock every ops-table-touching function they
    exercise directly (labels/planner/concurrency/executor/restore/prune), as
    before - only the "is this cluster registered" step is faked here.
    """
    from contextlib import contextmanager

    from starrocks_br.store.models import Cluster

    fake_cluster = Cluster(
        id=1,
        name="test-cluster",
        host="127.0.0.1",
        port=9030,
        user="root",
        password_encrypted="token",
        database="test_db",
        repository="test_repo",
    )
    fake_session = mocker.Mock(name="fake_cli_session")

    @contextmanager
    def _scope():
        yield fake_session

    mocker.patch("starrocks_br.cli.session_scope", _scope)
    mocker.patch("starrocks_br.cli.resolve_cluster", return_value=fake_cluster)
    return fake_cluster


@pytest.fixture
def mock_cluster_not_initialized(mocker):
    """Mock cli.py's cluster lookup as not-yet-registered (the "run init first" case).

    Replaces the old `mock_uninitialized_schema` fixture: `resolve_cluster`
    now raises `ClusterNotInitializedError` instead of auto-creating anything,
    since a hard `init` step is required before other commands will run.
    """
    from contextlib import contextmanager

    from starrocks_br import exceptions

    fake_session = mocker.Mock(name="fake_cli_session")

    @contextmanager
    def _scope():
        yield fake_session

    mocker.patch("starrocks_br.cli.session_scope", _scope)
    mocker.patch(
        "starrocks_br.cli.resolve_cluster",
        side_effect=exceptions.ClusterNotInitializedError("127.0.0.1:9030/test_db"),
    )


@pytest.fixture
def mock_healthy_cluster(mocker):
    """Mock a healthy cluster."""
    return mocker.patch("starrocks_br.health.check_cluster_health", return_value=(True, "Healthy"))


@pytest.fixture
def mock_unhealthy_cluster(mocker):
    """Mock an unhealthy cluster."""
    return mocker.patch(
        "starrocks_br.health.check_cluster_health", return_value=(False, "Cluster is unhealthy")
    )


@pytest.fixture
def mock_repo_exists(mocker):
    """Mock repository verification success."""
    return mocker.patch("starrocks_br.repository.ensure_repository")


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
