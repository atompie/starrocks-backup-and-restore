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

"""`starrocks-br api ...` - CLI commands that are thin HTTP clients of the
FastAPI server, separate from and additive to the existing direct-to-
StarRocks commands in `cli.py`. See specs/cli-api-client.
"""

import click

from .cluster import cluster_group
from .job import job_group
from .schedule import schedule_group


@click.group("api")
def api_group():
    """Commands that talk to a running starrocks-br API server over HTTP."""


@api_group.command("serve")
@click.option("--host", default="127.0.0.1", help="Bind address for the API server.")
@click.option("--port", default=8000, type=int, help="Bind port for the API server.")
def api_serve(host, port):
    """Start the FastAPI server (requires STARROCKS_BR_API_KEY and
    STARROCKS_BR_DB_ENCRYPTION_KEY to be set)."""
    import uvicorn

    from ..api.app import create_app

    uvicorn.run(create_app(), host=host, port=port)


api_group.add_command(cluster_group)
api_group.add_command(job_group)
api_group.add_command(schedule_group)
