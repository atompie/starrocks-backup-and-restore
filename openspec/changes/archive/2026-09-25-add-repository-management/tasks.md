## 1. Core: repository SQL helpers

- [x] 1.1 Add `list_repositories(db) -> list[dict]` to `repository.py`, parsing `SHOW REPOSITORIES` (RepoId, RepoName, CreateTime, IsReadOnly, Location, Broker, ErrMsg) into `{name, location, broker, is_read_only, error}` dicts; verify a unit test with a mocked `db.query` asserts correct field mapping for both tuple and dict row shapes
- [x] 1.2 Add `build_create_s3_repository_command(name, location, access_key, secret_key, endpoint, region) -> str` to `repository.py`, generating a `CREATE REPOSITORY ... WITH S3 ON LOCATION ... PROPERTIES (...)` statement (reuse `utils.quote_identifier`/`utils.quote_value` as existing modules do); verify a unit test asserts the exact generated SQL for representative inputs, including that no trailing slash produces a double-slash path (the bug found during the live smoke test in the parent `add-fastapi-server` change)
- [x] 1.3 Add `has_snapshots(db, repository_name) -> bool` to `repository.py`, wrapping `SHOW SNAPSHOT ON <repo>` and returning whether any row is present; verify unit tests for the empty and non-empty cases, and that a "repository not found" error from StarRocks is distinguished from "zero snapshots" rather than treated the same
- [x] 1.4 Add `drop_repository(db, name) -> None` to `repository.py`, executing `DROP REPOSITORY <name>`; verify a unit test asserts the exact SQL executed

## 2. API: repository routes

- [x] 2.1 Add `RepositoryCreate`, `RepositoryRead` Pydantic schemas to `api/schemas.py` (per the `api-repository-management` spec's field list); verify a test asserts `RepositoryRead` never includes credential fields (there are none to leak, but assert the schema has no `access_key`/`secret_key` fields) and `RepositoryCreate` requires name/location/access_key/secret_key/endpoint
- [x] 2.2 Implement `GET /clusters/{id}/repositories` in a new `api/routes/repositories.py`, connecting to the target cluster (reusing the `_connect`/`decrypt_password` pattern from `jobs/handlers.py`) and calling `repository.list_repositories`; verify against the spec's list scenarios (success, unknown cluster 404, unreachable cluster produces a distinguishable error rather than an empty list)
- [x] 2.3 Implement `POST /clusters/{id}/repositories`, calling `repository.build_create_s3_repository_command` + execute; verify against the spec's create scenarios (success, duplicate name 409, unknown cluster 404, missing required field 422) and confirm via a test that the submitted `access_key`/`secret_key` are never written to the metadata store (inspect the `Job`/`Cluster`/any new table after the call)
- [x] 2.4 Implement `DELETE /clusters/{id}/repositories/{name}`, checking `repository.has_snapshots` first and only calling `repository.drop_repository` when empty; verify against the spec's delete scenarios (blocked by existing snapshot -> 409, succeeds when empty, unknown cluster/repository -> 404)
- [x] 2.5 Register the new router in `api/app.py`'s `create_app()`; verify `GET /clusters/{id}/repositories` is reachable via a `TestClient` smoke test alongside the existing cluster/job/schedule routers

## 3. CLI: repository commands

- [x] 3.1 Add a `repository` Click subcommand group in a new `cli_api/repository.py` (`add`, `list`, `remove`), following the existing `cli_api/cluster.py` pattern, registered under the existing `api` group in `cli_api/__init__.py`; verify `starrocks-br api repository --help` lists all three without altering existing `api` subcommand output
- [x] 3.2 Implement `starrocks-br api repository add --cluster <id> --name ... --location ... --access-key ... --secret-key ... --endpoint ... [--region ...]`, calling `POST /clusters/{id}/repositories`; verify via an `httpx.MockTransport` test per the CLI-create scenario
- [x] 3.3 Implement `starrocks-br api repository list --cluster <id>`, calling `GET /clusters/{id}/repositories`; verify it prints each repository's name/location/error status
- [x] 3.4 Implement `starrocks-br api repository remove --cluster <id> <name>`, calling `DELETE /clusters/{id}/repositories/{name}`, surfacing a 409 (blocked by snapshots) as a clear non-zero-exit error without retrying; verify via the CLI-delete-blocked scenario test

## 4. Documentation

- [x] 4.1 Add a "Repositories" section to `docs/api.md` (endpoint reference table entries, request/response shapes, and the snapshot-check safety behavior on delete), and a CLI reference table row for each of the three new `api repository ...` commands, consistent with the existing sections for clusters/jobs/schedules
- [x] 4.2 Update `docs/configuration.md`'s repository section to note that `CREATE REPOSITORY`/`DROP REPOSITORY` can now also be done through the API/CLI (linking to `docs/api.md`), while keeping the existing manual-SQL examples for users not running the API server

## 5. End-to-end verification

- [x] 5.1 Run the full test suite and `ruff check .`; verify zero regressions versus the state at the end of the `add-fastapi-server` change (same pre-existing unrelated failures only)
- [x] 5.2 Manually verify against a real StarRocks instance with S3-compatible storage (same setup as the `add-fastapi-server` smoke test): create a repository via the API/CLI, list it back, attempt to delete it after a backup has been written to it (expect 409), then delete it once empty (expect success) — clean up all test data (repository, S3 objects) afterward, matching the cleanup discipline from the prior smoke test
