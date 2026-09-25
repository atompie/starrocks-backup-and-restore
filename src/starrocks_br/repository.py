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

from __future__ import annotations

from . import utils


class RepositoryNotFoundError(RuntimeError):
    """Raised when StarRocks reports a repository does not exist."""


def ensure_repository(db, name: str) -> None:
    """Verify that the specified repository exists and is accessible.

    Args:
        db: Database connection
        name: Repository name to verify

    Raises:
        RuntimeError: If repository doesn't exist or has errors
    """
    existing = _find_repository(db, name)
    if not existing:
        raise RuntimeError(
            f"Repository '{name}' not found. Please create it first using:\n"
            f"  CREATE REPOSITORY {name} WITH BROKER ON LOCATION '...' PROPERTIES(...)\n"
            f"For examples, see: https://docs.starrocks.io/docs/sql-reference/sql-statements/backup_restore/CREATE_REPOSITORY/"
        )

    # SHOW REPOSITORIES returns: RepoId, RepoName, CreateTime, IsReadOnly, Location, Broker, ErrMsg
    err_msg = existing[6]
    if err_msg and str(err_msg).strip().upper() not in {"", "NULL", "NONE"}:
        raise RuntimeError(f"Repository '{name}' has errors: {err_msg}")


def _find_repository(db, name: str):
    """Find a repository by name in SHOW REPOSITORIES output."""
    rows = db.query("SHOW REPOSITORIES")
    for row in rows:
        if row and row[1] == name:
            return row
    return None


def _field(row, dict_key: str, tuple_index: int):
    if isinstance(row, dict):
        return row.get(dict_key)
    return row[tuple_index]


def _normalize_error(raw_error) -> str | None:
    if raw_error is None:
        return None
    text = str(raw_error).strip()
    if text.upper() in {"", "NULL", "NONE"}:
        return None
    return text


def _parse_is_read_only(raw_value) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    return str(raw_value).strip().lower() == "true"


def list_repositories(db) -> list[dict]:
    """List every repository StarRocks currently knows about.

    Parses `SHOW REPOSITORIES` (RepoId, RepoName, CreateTime, IsReadOnly,
    Location, Broker, ErrMsg), accepting both tuple rows and dict rows since
    different db backends/mocks shape rows differently.
    """
    rows = db.query("SHOW REPOSITORIES")
    repositories = []
    for row in rows:
        repositories.append(
            {
                "name": _field(row, "RepoName", 1),
                "location": _field(row, "Location", 4),
                "broker": _field(row, "Broker", 5),
                "is_read_only": _parse_is_read_only(_field(row, "IsReadOnly", 3)),
                "error": _normalize_error(_field(row, "ErrMsg", 6)),
            }
        )
    return repositories


def build_create_s3_repository_command(
    name: str,
    location: str,
    access_key: str,
    secret_key: str,
    endpoint: str,
    region: str | None = None,
) -> str:
    """Build a `CREATE REPOSITORY ... WITH BROKER` statement for S3-compatible storage.

    StarRocks' grammar has no `WITH S3` clause (verified against a live 3.5.x
    cluster during the manual smoke test for this change: it only recognizes
    `WITH BROKER`, with the storage backend selected by the `aws.s3.*`
    properties) - S3-compatible repositories are created the same way as any
    other broker-backed repository.

    The location is stripped of any trailing slash before StarRocks appends
    its own path separator, avoiding the double-slash bug found during the
    live smoke test in the parent `add-fastapi-server` change.

    `aws.s3.enable_path_style_access` is always set to `true` for
    compatibility with non-AWS S3-compatible stores (e.g. MinIO, RustFS) that
    don't support virtual-hosted-style bucket addressing; AWS S3 itself still
    accepts path-style requests, so this doesn't break real AWS usage.
    `aws.s3.enable_ssl` is derived from the endpoint's scheme.
    """
    normalized_location = location.rstrip("/")

    properties = {
        "aws.s3.access_key": access_key,
        "aws.s3.secret_key": secret_key,
        "aws.s3.endpoint": endpoint,
        "aws.s3.enable_path_style_access": "true",
        "aws.s3.enable_ssl": "true" if endpoint.startswith("https://") else "false",
    }
    if region:
        properties["aws.s3.region"] = region

    properties_sql = ",\n    ".join(
        f"{utils.quote_value(key)} = {utils.quote_value(value)}" for key, value in properties.items()
    )

    return (
        f"CREATE REPOSITORY {utils.quote_identifier(name)}\n"
        f"WITH BROKER\n"
        f"ON LOCATION {utils.quote_value(normalized_location)}\n"
        f"PROPERTIES (\n    {properties_sql}\n)"
    )


def has_snapshots(db, repository_name: str) -> bool:
    """Return whether `repository_name` currently holds any snapshot.

    Raises:
        RepositoryNotFoundError: If StarRocks reports the repository itself
            doesn't exist, distinguished from the repository existing but
            simply having zero snapshots.
    """
    try:
        rows = db.query(f"SHOW SNAPSHOT ON {utils.quote_identifier(repository_name)}")
    except Exception as e:
        message = str(e).lower()
        if "repository" in message and ("unknown" in message or "not exist" in message or "not found" in message):
            raise RepositoryNotFoundError(f"Repository '{repository_name}' not found") from e
        raise
    return len(rows) > 0


def drop_repository(db, name: str) -> None:
    """Drop a repository by name."""
    db.execute(f"DROP REPOSITORY {utils.quote_identifier(name)}")
