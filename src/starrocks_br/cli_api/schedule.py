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

"""`starrocks-br api schedule ...` - the schedule registry plus the
single-shot `run-due` command meant to be invoked by an external cron
entry or Kubernetes CronJob, per specs/cli-api-client and design.md
Decision 5.
"""

import sys

import click

from .. import logger
from .client import add_common_api_options, make_client, request


@click.group("schedule")
def schedule_group():
    """Manage recurring backup schedules on the API server."""


@schedule_group.command("add")
@add_common_api_options
@click.option("--cluster", "cluster_id", required=True, type=int)
@click.option("--type", "job_type", required=True, type=click.Choice(["backup_full", "backup_incremental"]))
@click.option("--group", "group_name", required=True)
@click.option("--cadence", required=True, help="Cron expression, e.g. '0 1 * * *'.")
@click.option("--backend", help="Override the execution backend for this schedule.")
def schedule_add(api_url, api_key, cluster_id, job_type, group_name, cadence, backend):
    """Create a recurring backup schedule."""
    with make_client(api_url, api_key) as client:
        response = request(
            client,
            "POST",
            "/schedules",
            json={
                "cluster_id": cluster_id,
                "job_type": job_type,
                "group_name": group_name,
                "cadence": cadence,
                "backend": backend,
            },
        )
    body = response.json()
    logger.success(f"Created schedule {body['id']}, next run at {body['next_run_at']}")


@schedule_group.command("list")
@add_common_api_options
def schedule_list(api_url, api_key):
    """List all schedules."""
    with make_client(api_url, api_key) as client:
        response = request(client, "GET", "/schedules")
    for schedule in response.json():
        state = "enabled" if schedule["enabled"] else "disabled"
        logger.info(
            f"[{schedule['id']}] cluster={schedule['cluster_id']} {schedule['job_type']} "
            f"group={schedule['group_name']} cadence='{schedule['cadence']}' "
            f"next_run_at={schedule['next_run_at']} ({state})"
        )


@schedule_group.command("remove")
@add_common_api_options
@click.argument("schedule_id", type=int)
def schedule_remove(api_url, api_key, schedule_id):
    """Delete a schedule by id."""
    with make_client(api_url, api_key) as client:
        request(client, "DELETE", f"/schedules/{schedule_id}")
    logger.success(f"Removed schedule {schedule_id}")


@schedule_group.command("run-due")
@add_common_api_options
def schedule_run_due(api_url, api_key):
    """Trigger any schedules that are currently due.

    Intended to be invoked on a short fixed interval by an external cron
    entry or Kubernetes CronJob; each invocation calls the API once and
    exits.
    """
    with make_client(api_url, api_key) as client:
        response = request(client, "POST", "/schedules/run-due")

    body = response.json()
    logger.success(f"Triggered {body['triggered_count']} job(s): {body['triggered_job_ids']}")
    sys.exit(0)
