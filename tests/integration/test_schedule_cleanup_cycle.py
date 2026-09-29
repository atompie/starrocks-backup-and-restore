"""End-to-end schedule cleanup against a real StarRocks cluster and S3 store.

Exercises the whole stack with nothing mocked: register a cluster, create an S3
repository, define an inventory group, create a one-shot schedule (which submits an
immediate full backup), then delete that schedule and confirm the asynchronous
`schedule_cleanup` job drops the snapshot and removes the schedule's backup job and
the schedule itself.

Requires a StarRocks cluster on 127.0.0.1:9030 and an S3-compatible store (e.g.
MinIO) on 127.0.0.1:9000 with a bucket named "test" - see `tests/integration/conftest.py`
for connection details. The whole module is skipped if either isn't reachable.

Note: as of this writing, some local StarRocks builds reject `DROP SNAPSHOT ON <repo>
WHERE SNAPSHOT = <label>` with "No viable statement for input 'DROP SNAPSHOT'" even
though `SHOW SNAPSHOT` works fine against the same repository. That is a pre-existing
gap in `dal/db/prune.py::execute_drop_snapshot` (shared by the `prune` job type, not
introduced by schedule cleanup) rather than a bug in this test or in
`commands/schedules.py::run_schedule_cleanup` - if this test fails at the drop-snapshot
step, check `DROP SNAPSHOT` support on the target StarRocks build before suspecting
this change.
"""

import time
import uuid

import pytest

from .conftest import S3_ACCESS_KEY, S3_BUCKET, S3_ENDPOINT_FROM_STARROCKS, S3_SECRET_KEY

JOB_POLL_INTERVAL_SECONDS = 1
JOB_POLL_TIMEOUT_SECONDS = 120


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
    suffix = uuid.uuid4().hex[:8]
    return {
        "suffix": suffix,
        "database": f"it_cleanup_{suffix}",
        "table": "orders",
        "cluster": f"it_cleanup_cluster_{suffix}",
        "repository": f"it_cleanup_repo_{suffix}",
        "group": f"it_cleanup_group_{suffix}",
    }


@pytest.fixture
def seeded_database(sr_admin_db, it_names):
    """Create the test database/table with data, and drop it again afterwards."""
    database = it_names["database"]
    table = it_names["table"]

    sr_admin_db.execute(f"DROP DATABASE IF EXISTS `{database}`")
    sr_admin_db.execute(f"CREATE DATABASE `{database}`")
    sr_admin_db.execute(
        f"""CREATE TABLE `{database}`.`{table}` (
            id INT,
            name VARCHAR(64)
        )
        DISTRIBUTED BY HASH(id) BUCKETS 1
        PROPERTIES ("replication_num" = "1")"""
    )
    sr_admin_db.execute(f"INSERT INTO `{database}`.`{table}` VALUES (1, 'widget')")

    yield it_names

    sr_admin_db.execute(f"DROP DATABASE IF EXISTS `{database}`")


def test_schedule_cleanup_drops_snapshot_and_removes_schedule(
    api_client, sr_admin_db, seeded_database, require_starrocks_reachable_s3
):
    database = seeded_database["database"]
    repository_name = seeded_database["repository"]

    cluster_resp = api_client.post(
        "/cluster",
        json={
            "name": seeded_database["cluster"],
            "host": "127.0.0.1",
            "port": 9030,
            "user": "root",
            "password": "",
        },
    )
    assert cluster_resp.status_code == 201, cluster_resp.text
    cluster_id = cluster_resp.json()["id"]

    repo_resp = api_client.post(
        f"/repositories/cluster/{cluster_id}",
        json={
            "name": repository_name,
            "location": f"s3://{S3_BUCKET}/it-cleanup/{seeded_database['suffix']}",
            "access_key": S3_ACCESS_KEY,
            "secret_key": S3_SECRET_KEY,
            "endpoint": S3_ENDPOINT_FROM_STARROCKS,
            "region": "us-east-1",
        },
    )
    assert repo_resp.status_code == 201, repo_resp.text

    group_resp = api_client.post(
        f"/inventories/cluster/{cluster_id}",
        json={"name": seeded_database["group"], "tables": [{"database": database, "table": "*"}]},
    )
    assert group_resp.status_code == 201, group_resp.text
    group_id = group_resp.json()["id"]

    # One-shot schedule: manual full backup submission is retired, so this is the only
    # way left to submit an immediate full backup.
    schedule_resp = api_client.post(
        f"/backup/schedules/cluster/{cluster_id}",
        json={"job_type": "backup_full", "inventory_group_id": group_id, "repository": repository_name},
    )
    assert schedule_resp.status_code == 201, schedule_resp.text
    schedule = schedule_resp.json()
    backup_job_id = schedule["last_run_job_id"]

    backup_job = _wait_for_job(api_client, backup_job_id)
    assert backup_job["status"] == "SUCCESS", backup_job
    import json

    backup_label = json.loads(backup_job["result_json"])["label"]

    # Confirm the snapshot actually exists before cleanup.
    snapshot_rows = sr_admin_db.query(
        f"SHOW SNAPSHOT ON `{repository_name}` WHERE SNAPSHOT = '{backup_label}'"
    )
    assert snapshot_rows

    delete_resp = api_client.delete(f"/backup/schedules/cluster/{cluster_id}/schedule_id/{schedule['id']}")
    assert delete_resp.status_code == 202, delete_resp.text
    cleanup_job_id = delete_resp.json()["id"]

    cleanup_job = _wait_for_job(api_client, cleanup_job_id)
    assert cleanup_job["status"] == "SUCCESS", cleanup_job

    # The snapshot was dropped from the repository.
    remaining_snapshots = sr_admin_db.query(
        f"SHOW SNAPSHOT ON `{repository_name}` WHERE SNAPSHOT = '{backup_label}'"
    )
    assert not remaining_snapshots

    # The schedule and its backup job's metadata are gone; the cleanup job itself remains.
    assert (
        api_client.get(f"/backup/schedules/cluster/{cluster_id}/schedule_id/{schedule['id']}").status_code
        == 404
    )
    assert api_client.get(f"/job/{backup_job_id}").status_code == 404
    assert api_client.get(f"/job/{cleanup_job_id}").status_code == 200
