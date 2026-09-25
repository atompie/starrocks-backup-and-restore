"""The single implementation of repository create/delete business rules.

Repository listing/creation/deletion are live pass-throughs to the target
StarRocks cluster (per specs/api-repository-management) - the caller is
responsible for connecting (`database`) and closing that connection; this
module only holds the decision logic (conflict/retention checks) shared by
however many adapters call it.
"""

from .. import repository
from ..exceptions import RepositoryAlreadyExistsError, RepositoryStillHasSnapshotsError


def create_repository(
    database,
    cluster_name: str,
    name: str,
    location: str,
    access_key: str,
    secret_key: str,
    endpoint: str | None,
    region: str | None,
) -> dict:
    command = repository.build_create_s3_repository_command(
        name=name,
        location=location,
        access_key=access_key,
        secret_key=secret_key,
        endpoint=endpoint,
        region=region,
    )
    try:
        database.execute(command)
    except Exception as e:
        # StarRocks' actual wording is "already exist" (verified against a
        # live cluster during the manual smoke test), not "already exists".
        if "already exist" in str(e).lower():
            raise RepositoryAlreadyExistsError(name, cluster_name) from e
        raise

    for repo in repository.list_repositories(database):
        if repo["name"] == name:
            return repo

    # StarRocks accepted the CREATE REPOSITORY statement but does not
    # (yet) list it back - report what we know without guessing fields.
    return {
        "name": name,
        "location": location,
        "broker": None,
        "is_read_only": False,
        "error": None,
    }


def delete_repository(database, name: str) -> None:
    """Delete `name`, refusing when it still holds snapshot data.

    Raises `repository.RepositoryNotFoundError` (unchanged, from
    `repository.has_snapshots`) when the repository doesn't exist.
    """
    if repository.has_snapshots(database, name):
        raise RepositoryStillHasSnapshotsError(name)

    repository.drop_repository(database, name)
