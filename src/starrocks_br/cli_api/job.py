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

import sys
import time

import click

from .. import logger
from .client import add_common_api_options, make_client, request

_ENDPOINT_BY_TYPE = {
    "backup-full": "backups/full",
    "backup-incremental": "backups/incremental",
    "restore": "restores",
    "prune": "prunes",
}


@click.group("job")
def job_group():
    """Submit and monitor jobs against a registered cluster."""


@job_group.command("submit")
@add_common_api_options
@click.option("--cluster", "cluster_id", required=True, type=int, help="Registered cluster id.")
@click.option(
    "--type",
    "job_type",
    required=True,
    type=click.Choice(list(_ENDPOINT_BY_TYPE)),
    help="Kind of job to submit.",
)
@click.option("--group", help="Inventory group (backups) or restore filter.")
@click.option("--name", help="Optional custom backup label.")
@click.option("--baseline-backup", help="Baseline backup label (incremental backups).")
@click.option("--target-label", help="Backup label to restore (restore jobs).")
@click.option("--table", help="Single table to restore (restore jobs).")
@click.option("--rename-suffix", default="_restored", help="Temporary-table suffix (restore jobs).")
@click.option("--keep-last", type=int, help="Prune: keep last N backups.")
@click.option("--older-than", help="Prune: delete backups older than this timestamp.")
@click.option("--snapshot", help="Prune: delete this specific snapshot.")
@click.option("--snapshots", help="Prune: delete these comma-separated snapshots.")
@click.option("--dry-run", is_flag=True, help="Prune: show what would be deleted.")
@click.option("--backend", help="Override the execution backend for this job.")
@click.option("--wait", is_flag=True, help="Poll until the job reaches a terminal state.")
def job_submit(
    api_url,
    api_key,
    cluster_id,
    job_type,
    group,
    name,
    baseline_backup,
    target_label,
    table,
    rename_suffix,
    keep_last,
    older_than,
    snapshot,
    snapshots,
    dry_run,
    backend,
    wait,
):
    """Submit a backup/restore/prune job against a registered cluster."""
    payload = {
        "group": group,
        "name": name,
        "baseline_backup": baseline_backup,
        "target_label": target_label,
        "table": table,
        "rename_suffix": rename_suffix,
        "keep_last": keep_last,
        "older_than": older_than,
        "snapshot": snapshot,
        "snapshots": snapshots,
        "dry_run": dry_run,
        "backend": backend,
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    with make_client(api_url, api_key) as client:
        response = request(client, "POST", f"/clusters/{cluster_id}/{_ENDPOINT_BY_TYPE[job_type]}", json=payload)
        job = response.json()
        logger.success(f"Submitted job {job['id']} (status={job['status']})")

        if not wait:
            return

        while job["status"] in ("PENDING", "RUNNING"):
            time.sleep(2)
            job = request(client, "GET", f"/jobs/{job['id']}").json()
            progress = f" progress={job['progress_pct']}%" if job["progress_pct"] is not None else ""
            logger.progress(f"Job {job['id']}: {job['status']}{progress}")

    if job["status"] == "SUCCESS":
        logger.success(f"Job {job['id']} completed successfully")
        sys.exit(0)
    else:
        logger.error(f"Job {job['id']} failed: {job.get('error_message')}")
        sys.exit(1)


@job_group.command("status")
@add_common_api_options
@click.argument("job_id", type=int)
def job_status(api_url, api_key, job_id):
    """Show a job's current status and progress."""
    with make_client(api_url, api_key) as client:
        job = request(client, "GET", f"/jobs/{job_id}").json()

    progress = f", progress={job['progress_pct']}%" if job["progress_pct"] is not None else ""
    logger.info(f"Job {job['id']}: {job['status']}{progress}")
    if job.get("error_message"):
        logger.error(job["error_message"])
