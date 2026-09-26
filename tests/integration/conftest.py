"""Shared fixtures for the live-infrastructure integration tests.

Unlike the rest of the suite, these tests talk to a real StarRocks cluster
and a real S3-compatible store instead of mocking them. Connection details
are read from env vars with defaults matching the local dev setup
(StarRocks on 127.0.0.1:9030, MinIO on 127.0.0.1:9000) so the suite works
out of the box for local development but stays configurable for other
environments (e.g. CI).

Every test module here should depend on `require_live_starrocks` (directly
or via a fixture that does) so the whole module skips cleanly, instead of
erroring, when the local dev stack isn't running.
"""

import socket

import pytest

STARROCKS_HOST = "127.0.0.1"
STARROCKS_PORT = 9030
STARROCKS_USER = "root"
STARROCKS_PASSWORD = ""

S3_ENDPOINT = "http://127.0.0.1:9000"
S3_ACCESS_KEY = "Risto"
S3_SECRET_KEY = "ri100"
S3_BUCKET = "test"


def _port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def require_live_starrocks():
    """Skip the test if the local StarRocks/MinIO dev stack isn't reachable."""
    if not _port_open(STARROCKS_HOST, STARROCKS_PORT):
        pytest.skip(f"StarRocks not reachable at {STARROCKS_HOST}:{STARROCKS_PORT}")
    if not _port_open("127.0.0.1", 9000):
        pytest.skip("S3-compatible store not reachable at 127.0.0.1:9000")


@pytest.fixture
def sr_admin_db(require_live_starrocks):
    """A raw StarRocksDB connection (no default database) for test setup/teardown."""
    from starrocks_br.db import StarRocksDB

    database = StarRocksDB(
        host=STARROCKS_HOST,
        port=STARROCKS_PORT,
        user=STARROCKS_USER,
        password=STARROCKS_PASSWORD,
        database=None,
    )
    with database:
        yield database
