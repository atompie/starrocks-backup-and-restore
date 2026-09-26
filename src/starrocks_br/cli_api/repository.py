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

import click

from .. import logger
from .client import add_common_api_options, make_client, request


@click.group("repository")
def repository_group():
    """Manage backup repositories on a registered StarRocks cluster."""


@repository_group.command("add")
@add_common_api_options
@click.option("--cluster", "cluster_id", required=True, type=int, help="Registered cluster id.")
@click.option("--name", required=True, help="Repository name.")
@click.option("--location", required=True, help="S3 location (e.g. s3://bucket/path).")
@click.option("--access-key", required=True, help="S3 access key.")
@click.option("--secret-key", required=True, help="S3 secret key.")
@click.option("--endpoint", required=True, help="S3 endpoint.")
@click.option("--region", help="S3 region.")
def repository_add(api_url, api_key, cluster_id, name, location, access_key, secret_key, endpoint, region):
    """Create an S3-compatible repository on a registered cluster."""
    payload = {
        "name": name,
        "location": location,
        "access_key": access_key,
        "secret_key": secret_key,
        "endpoint": endpoint,
    }
    if region is not None:
        payload["region"] = region

    with make_client(api_url, api_key) as client:
        request(client, "POST", f"/repositories/cluster/{cluster_id}", json=payload)
    logger.success(f"Created repository '{name}' on cluster {cluster_id}")


@repository_group.command("list")
@add_common_api_options
@click.option("--cluster", "cluster_id", required=True, type=int, help="Registered cluster id.")
def repository_list(api_url, api_key, cluster_id):
    """List repositories on a registered cluster."""
    with make_client(api_url, api_key) as client:
        response = request(client, "GET", f"/repositories/cluster/{cluster_id}")
    for repo in response.json():
        status = f"error={repo['error']}" if repo.get("error") else "ok"
        logger.info(f"{repo['name']} - {repo['location']} ({status})")


@repository_group.command("remove")
@add_common_api_options
@click.option("--cluster", "cluster_id", required=True, type=int, help="Registered cluster id.")
@click.argument("name")
def repository_remove(api_url, api_key, cluster_id, name):
    """Delete a repository from a registered cluster (must hold no snapshots)."""
    with make_client(api_url, api_key) as client:
        request(client, "DELETE", f"/repositories/cluster/{cluster_id}/name/{name}")
    logger.success(f"Removed repository '{name}' from cluster {cluster_id}")
