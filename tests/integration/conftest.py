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

import os
import socket
import subprocess

import pytest

STARROCKS_HOST = "127.0.0.1"
STARROCKS_PORT = 9030
STARROCKS_USER = "root"
STARROCKS_PASSWORD = ""

S3_ENDPOINT = "http://127.0.0.1:9000"
S3_ACCESS_KEY = "rustfsadmin"
S3_SECRET_KEY = "rustfsadmin"
S3_BUCKET = "test"

# The API server process talks to S3 for `/repositories/verify` directly from
# the host, so `S3_ENDPOINT` (127.0.0.1) works there. But `POST
# /repositories/cluster/{id}` makes StarRocks itself run `CREATE REPOSITORY`,
# and StarRocks runs in its own container - on the default Docker bridge
# network "127.0.0.1" from inside that container is the container itself,
# not the host, so the S3 store must be addressed by its container IP
# instead. Resolved once per session via `docker inspect` so it survives the
# S3 container being recreated; override via env var for setups where the
# containers share a network with a real DNS alias (e.g. CI, or
# `rustfs.docker.host`).
S3_DOCKER_CONTAINER = os.environ.get("STARROCKS_BR_TEST_S3_CONTAINER", "rustfs")


def _docker_container_ip(container: str) -> str | None:
    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "-f",
                "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                container,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    ip = result.stdout.strip()
    return ip or None


def _resolve_s3_endpoint_from_starrocks() -> str | None:
    override = os.environ.get("STARROCKS_BR_TEST_S3_ENDPOINT_FROM_STARROCKS")
    if override:
        return override
    ip = _docker_container_ip(S3_DOCKER_CONTAINER)
    return f"http://{ip}:9000" if ip else None


S3_ENDPOINT_FROM_STARROCKS = _resolve_s3_endpoint_from_starrocks()


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


@pytest.fixture(scope="session")
def require_starrocks_reachable_s3(require_live_starrocks):
    """Skip if the S3 store's container IP couldn't be resolved for StarRocks to use."""
    if S3_ENDPOINT_FROM_STARROCKS is None:
        pytest.skip(
            f"Could not resolve a StarRocks-reachable S3 endpoint (docker container "
            f"'{S3_DOCKER_CONTAINER}' not found) - set "
            f"STARROCKS_BR_TEST_S3_ENDPOINT_FROM_STARROCKS to override"
        )


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
