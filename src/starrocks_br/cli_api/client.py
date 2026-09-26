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

"""Thin HTTP client helper shared by all `starrocks-br api ...` subcommands.

Per specs/cli-api-client, these commands are a separate surface from the
existing direct-to-StarRocks CLI: they never talk to StarRocks themselves,
only to a running API server over HTTP.
"""

import os

import click
import httpx

API_URL_ENV_VAR = "STARROCKS_BR_API_URL"
API_KEY_ENV_VAR = "STARROCKS_BR_API_KEY"


class ApiClientError(click.ClickException):
    """Raised for any failure talking to the API server; exits non-zero with a clear message."""


def resolve_api_url(api_url: str | None) -> str:
    url = api_url or os.getenv(API_URL_ENV_VAR)
    if not url:
        raise ApiClientError(
            f"API server URL is required. Pass --api-url or set {API_URL_ENV_VAR}."
        )
    return url.rstrip("/")


def resolve_api_key(api_key: str | None) -> str:
    key = api_key or os.getenv(API_KEY_ENV_VAR)
    if not key:
        raise ApiClientError(
            f"API key is required. Pass --api-key or set {API_KEY_ENV_VAR}."
        )
    return key


def make_client(api_url: str | None, api_key: str | None) -> httpx.Client:
    url = resolve_api_url(api_url)
    key = resolve_api_key(api_key)
    return httpx.Client(base_url=url, headers={"Authorization": f"Bearer {key}"}, timeout=30.0)


def request(client: httpx.Client, method: str, path: str, **kwargs) -> httpx.Response:
    """Issue a request and turn connection/auth/server errors into ApiClientError."""
    try:
        response = client.request(method, path, **kwargs)
    except httpx.RequestError as e:
        raise ApiClientError(f"Could not reach API server at {client.base_url}: {e}") from e

    if response.status_code == 401:
        raise ApiClientError("API server rejected the request: invalid or missing API key")
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            detail = response.text
        raise ApiClientError(f"API request failed ({response.status_code}): {detail}")

    return response


def resolve_group_id(client: httpx.Client, cluster_id: int, name: str) -> int:
    """Resolve an inventory group name to its id, scoped to `cluster_id`.

    The API is id-keyed throughout (per specs/api-inventory-groups), so the
    CLI - which still takes a human-readable `--group <name>` - lists the
    cluster's groups and matches by name client-side rather than the API
    exposing a name-based lookup route.
    """
    response = request(client, "GET", f"/cluster/{cluster_id}/inventory-groups")
    for group in response.json():
        if group["name"] == name:
            return group["id"]
    raise ApiClientError(f"Inventory group '{name}' not found on cluster {cluster_id}")


common_api_options = [
    click.option("--api-url", help=f"API server URL. Defaults to {API_URL_ENV_VAR}."),
    click.option("--api-key", help=f"API key. Defaults to {API_KEY_ENV_VAR}."),
]


def add_common_api_options(f):
    for option in reversed(common_api_options):
        f = option(f)
    return f
