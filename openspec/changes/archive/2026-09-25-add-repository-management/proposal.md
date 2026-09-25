## Why

Registering a StarRocks cluster via the API requires naming a backup repository, but nothing in the tool creates, lists, or removes repositories — operators must run `CREATE REPOSITORY` by hand via SQL against each cluster (as documented in `docs/configuration.md`), and there is no way to see what repositories already exist on a cluster or to clean one up through the API/CLI. This leaves "where does this cluster's data actually go" opaque to anyone only using the API, and makes repository lifecycle a manual, StarRocks-console-only operation even though everything else (clusters, jobs, schedules) is now API-driven.

## What Changes

- Add a new capability, `api-repository-management`, exposing repository operations scoped to a registered cluster: list all repositories that exist on that cluster (live, via `SHOW REPOSITORIES`), create a new S3-compatible repository on it, and delete one.
- Repository listing is a live pass-through to the target StarRocks cluster — no new local state is introduced; this mirrors how `Cluster.repository` already only stores a name, never location/credentials.
- Repository creation accepts S3-compatible connection details (location, access key, secret key, endpoint, region) in the request and passes them straight through to StarRocks' `CREATE REPOSITORY ... WITH S3 ...`; the API does not persist these credentials anywhere (StarRocks itself doesn't return them via `SHOW REPOSITORIES` either, so there would be nothing to display back).
- Repository deletion first checks StarRocks' own `SHOW SNAPSHOT ON <repo>` for that repository and rejects the deletion if any snapshot exists, so an operator can't accidentally orphan real backup data; only an empty repository can be dropped through this endpoint.
- Add matching `starrocks-br api repository add/list/remove` CLI subcommands (thin HTTP clients, consistent with the existing `api cluster`/`api job`/`api schedule` command groups).

## Capabilities

### New Capabilities
- `api-repository-management`: list, create, and safely delete StarRocks backup repositories on a registered cluster through the API and CLI.

### Modified Capabilities
(none — this only adds new endpoints/commands; `api-cluster-registry`'s existing behavior, including the `repository` field on a `Cluster`, is unchanged)

## Impact

- **New code**: `api/routes/repositories.py` (list/create/delete endpoints nested under `/clusters/{id}/repositories`), corresponding Pydantic schemas, and a `core` helper (alongside the existing `repository.py`) for building/executing `CREATE REPOSITORY`/`DROP REPOSITORY`/`SHOW SNAPSHOT` SQL; a new `cli_api/repository.py` CLI command group, registered under the existing `api` group.
- **Reused unchanged**: the existing cluster-connection/decrypt-password pattern already used by job handlers (`jobs/handlers.py`'s `_connect`/`decrypt_password`) is reused to open a connection to the target cluster for these operations; no new credential storage, no SQLAlchemy model changes.
- **Unaffected**: `api-cluster-registry`, `api-job-execution`, `api-scheduling`, `cli-api-client` capabilities and their existing endpoints/commands; the direct-to-StarRocks CLI (`backup`, `restore`, `prune`, `init`) and `config.yaml` are untouched.
- **Known limitation carried forward from exploration** (not addressed by this change): a backup job that is `RUNNING`/mid-upload may not yet be visible via `SHOW SNAPSHOT`, so a concurrent delete could race an in-flight backup to the same repository. Documented as a known gap, not fixed here.
