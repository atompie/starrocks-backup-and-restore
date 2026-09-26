"""End-to-end full backup/restore cycle against a real StarRocks cluster and S3 store.

Exercises the whole stack with nothing mocked, through the same FastAPI app the
CLI's `api` subcommands talk to: register a cluster, create an S3 repository,
define an inventory group, run a full backup, destroy the data, and restore it.

Requires a StarRocks cluster on 127.0.0.1:9030 and an S3-compatible store (e.g.
MinIO) on 127.0.0.1:9000 with a bucket named "test" - see `tests/integration/conftest.py`
for connection details. The whole module is skipped if either isn't reachable.
"""

import time
import uuid

import pytest

from .conftest import S3_ACCESS_KEY, S3_BUCKET, S3_ENDPOINT, S3_SECRET_KEY

JOB_POLL_INTERVAL_SECONDS = 1
JOB_POLL_TIMEOUT_SECONDS = 120

ORIGINAL_ROWS = [
    (1, "widget", "9.99"),
    (2, "gadget", "19.50"),
    (3, "gizmo", "42.00"),
]


def _table_ddl(database: str, table: str) -> str:
    return f"""CREATE TABLE `{database}`.`{table}` (
        id INT,
        name VARCHAR(64),
        amount DECIMAL(10, 2)
    )
    DISTRIBUTED BY HASH(id) BUCKETS 1
    PROPERTIES ("replication_num" = "1")"""


def _wait_for_job(api_client, job_id: int) -> dict:
    deadline = time.monotonic() + JOB_POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        job = api_client.get(f"/job/{job_id}").json()
        if job["status"] in ("SUCCESS", "FAILED"):
            return job
        time.sleep(JOB_POLL_INTERVAL_SECONDS)
    raise AssertionError(f"Job {job_id} did not reach a terminal state within {JOB_POLL_TIMEOUT_SECONDS}s")


@pytest.fixture
def it_names():
    """A unique name suffix so repeated runs never collide with each other."""
    suffix = uuid.uuid4().hex[:8]
    return {
        "suffix": suffix,
        "database": f"it_br_{suffix}",
        "table": "orders",
        "cluster": f"it_cluster_{suffix}",
        "repository": f"it_repo_{suffix}",
        "group": f"it_group_{suffix}",
        "backup_label": f"it_full_{suffix}",
    }


@pytest.fixture
def seeded_database(sr_admin_db, it_names):
    """Create the test database/table with data, and drop it again afterwards."""
    database = it_names["database"]
    table = it_names["table"]

    sr_admin_db.execute(f"DROP DATABASE IF EXISTS `{database}`")
    sr_admin_db.execute(f"CREATE DATABASE `{database}`")
    sr_admin_db.execute(_table_ddl(database, table))
    values = ", ".join(f"({id_}, '{name}', {amount})" for id_, name, amount in ORIGINAL_ROWS)
    sr_admin_db.execute(f"INSERT INTO `{database}`.`{table}` VALUES {values}")

    yield it_names

    sr_admin_db.execute(f"DROP DATABASE IF EXISTS `{database}`")


def test_full_backup_then_restore_recovers_dropped_database(api_client, sr_admin_db, seeded_database):
    database = seeded_database["database"]
    table = seeded_database["table"]

    # 1. Register the cluster with the API server.
    cluster_resp = api_client.post(
        "/cluster",
        json={
            "name": seeded_database["cluster"],
            "host": "127.0.0.1",
            "port": 9030,
            "user": "root",
            "password": "",
            "database": database,
            "repository": seeded_database["repository"],
        },
    )
    assert cluster_resp.status_code == 201, cluster_resp.text
    cluster_id = cluster_resp.json()["id"]

    # 2. Create a real S3-backed repository on the cluster.
    repo_resp = api_client.post(
        f"/cluster/{cluster_id}/repositories",
        json={
            "name": seeded_database["repository"],
            "location": f"s3://{S3_BUCKET}/it-backups/{seeded_database['suffix']}",
            "access_key": S3_ACCESS_KEY,
            "secret_key": S3_SECRET_KEY,
            "endpoint": S3_ENDPOINT,
            "region": "us-east-1",
        },
    )
    assert repo_resp.status_code == 201, repo_resp.text

    # 3. Define an inventory group covering every table in the test database.
    group_resp = api_client.post(
        f"/cluster/{cluster_id}/inventory-groups",
        json={"name": seeded_database["group"], "tables": [{"database": database, "table": "*"}]},
    )
    assert group_resp.status_code == 201, group_resp.text
    group_id = group_resp.json()["id"]

    # 4. Run a full backup.
    backup_resp = api_client.post(
        f"/cluster/{cluster_id}/backups/full",
        json={"group_id": group_id, "name": seeded_database["backup_label"]},
    )
    assert backup_resp.status_code == 202, backup_resp.text
    backup_job = _wait_for_job(api_client, backup_resp.json()["id"])
    assert backup_job["status"] == "SUCCESS", backup_job

    # 5. Simulate total data loss: drop the whole database.
    #
    # StarRocks' RESTORE requires the target database to already exist, and this
    # tool's restore flow renames the *existing* table out of the way before
    # putting the restored one in its place (see `restore._perform_atomic_rename`)
    # - so recovering from a fully dropped database means recreating the (empty)
    # database and table shell first, exactly as an operator would when rebuilding
    # a cluster from scratch before restoring onto it.
    sr_admin_db.execute(f"DROP DATABASE `{database}`")
    sr_admin_db.execute(f"CREATE DATABASE `{database}`")
    sr_admin_db.execute(_table_ddl(database, table))

    remaining = sr_admin_db.query(f"SELECT COUNT(*) FROM `{database}`.`{table}`")
    assert remaining[0][0] == 0

    # 6. Restore from the full backup.
    restore_resp = api_client.post(
        f"/cluster/{cluster_id}/restores",
        json={"target_label": seeded_database["backup_label"], "group_id": group_id},
    )
    assert restore_resp.status_code == 202, restore_resp.text
    restore_job = _wait_for_job(api_client, restore_resp.json()["id"])
    assert restore_job["status"] == "SUCCESS", restore_job

    # 7. The original rows are back.
    restored_rows = sr_admin_db.query(f"SELECT id, name, amount FROM `{database}`.`{table}` ORDER BY id")
    restored = [(row[0], row[1], str(row[2])) for row in restored_rows]
    assert restored == ORIGINAL_ROWS
