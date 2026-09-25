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


@click.group("cluster")
def cluster_group():
    """Manage the API server's registered StarRocks clusters."""


@cluster_group.command("add")
@add_common_api_options
@click.option("--name", required=True, help="Unique name for this cluster registration.")
@click.option("--host", required=True)
@click.option("--port", required=True, type=int)
@click.option("--user", required=True)
@click.option("--password", required=True, help="StarRocks user password (stored encrypted).")
@click.option("--database", required=True, help="Default database on this cluster.")
@click.option("--repository", required=True, help="StarRocks backup repository name.")
@click.option("--ops-database", default="ops", help="Ops schema database name (default: ops).")
@click.option("--default-backend", default="thread", help="Default job execution backend.")
def cluster_add(
    api_url, api_key, name, host, port, user, password, database, repository, ops_database, default_backend
):
    """Register a new StarRocks cluster with the API server."""
    with make_client(api_url, api_key) as client:
        response = request(
            client,
            "POST",
            "/clusters",
            json={
                "name": name,
                "host": host,
                "port": port,
                "user": user,
                "password": password,
                "database": database,
                "repository": repository,
                "ops_database": ops_database,
                "default_backend": default_backend,
            },
        )
    body = response.json()
    logger.success(f"Registered cluster '{name}' with id {body['id']}")


@cluster_group.command("list")
@add_common_api_options
def cluster_list(api_url, api_key):
    """List clusters registered with the API server."""
    with make_client(api_url, api_key) as client:
        response = request(client, "GET", "/clusters")
    for cluster in response.json():
        logger.info(
            f"[{cluster['id']}] {cluster['name']} - {cluster['host']}:{cluster['port']} "
            f"(db={cluster['database']}, repo={cluster['repository']})"
        )


@cluster_group.command("remove")
@add_common_api_options
@click.argument("cluster_id", type=int)
def cluster_remove(api_url, api_key, cluster_id):
    """Remove a registered cluster by id."""
    with make_client(api_url, api_key) as client:
        request(client, "DELETE", f"/clusters/{cluster_id}")
    logger.success(f"Removed cluster {cluster_id}")
