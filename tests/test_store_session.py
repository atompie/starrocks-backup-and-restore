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

import pytest

from starrocks_br.store import session as session_module
from starrocks_br.store.models import Base, Cluster


@pytest.fixture
def sqlite_env(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("STARROCKS_BR_DATABASE_URL", f"sqlite:///{db_path}")
    session_module.reset_engine_cache()
    yield
    session_module.reset_engine_cache()


def test_get_database_url_defaults_to_local_sqlite(monkeypatch):
    monkeypatch.delenv("STARROCKS_BR_DATABASE_URL", raising=False)
    assert session_module.get_database_url() == session_module.DEFAULT_DATABASE_URL


def test_get_database_url_reads_env_override(monkeypatch):
    monkeypatch.setenv("STARROCKS_BR_DATABASE_URL", "mysql+pymysql://user:pass@host/db")
    assert session_module.get_database_url() == "mysql+pymysql://user:pass@host/db"


def test_session_scope_round_trip(sqlite_env):
    engine = session_module.get_engine()
    Base.metadata.create_all(engine)

    with session_module.session_scope() as session:
        session.add(
            Cluster(
                name="roundtrip",
                host="h",
                port=9030,
                user="u",
                password_encrypted="enc",
            )
        )

    with session_module.session_scope() as session:
        result = session.query(Cluster).filter_by(name="roundtrip").one()
        assert result.host == "h"


def test_session_scope_rolls_back_on_error(sqlite_env):
    engine = session_module.get_engine()
    Base.metadata.create_all(engine)

    with pytest.raises(ValueError):
        with session_module.session_scope() as session:
            session.add(
                Cluster(
                    name="will-rollback",
                    host="h",
                    port=9030,
                    user="u",
                    password_encrypted="enc",
                )
            )
            raise ValueError("boom")

    with session_module.session_scope() as session:
        assert session.query(Cluster).filter_by(name="will-rollback").first() is None
