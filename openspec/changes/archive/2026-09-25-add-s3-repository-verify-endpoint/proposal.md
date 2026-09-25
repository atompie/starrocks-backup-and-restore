## Why

Creating an S3 repository via `POST /cluster/{id}/repositories` currently fails only after StarRocks itself rejects the `CREATE REPOSITORY` statement, which can surface as an opaque `500` when the real cause is a network or credential problem unrelated to StarRocks (e.g. the StarRocks container being unable to reach an endpoint the API host can reach fine). Operators need a way to check S3 endpoint reachability and credential validity directly from the API server, independent of any registered cluster, before attempting repository creation.

## What Changes

- Add a standalone `POST /repositories/verify` endpoint that checks S3 endpoint reachability and credential validity using `boto3`'s `head_bucket`, with no cluster context required.
- Add `boto3` as a new dependency (`api` optional-dependencies group).
- New `RepositoryVerifyRequest`/`RepositoryVerifyResponse` schemas; the response never includes `secret_key`, matching the existing cluster-verify redaction contract.
- Always return HTTP 200 with `{success, message}` for both success and failure (auth failure, bucket not found, unreachable endpoint), never a raw 500.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `api-repository-management`: adds a new requirement for standalone S3 connectivity/credential verification, independent of any registered cluster (existing requirements only cover cluster-scoped repository listing/creation/deletion).

## Impact

- `pyproject.toml`: new `boto3` dependency.
- `src/starrocks_br/api/schemas.py`: new `RepositoryVerifyRequest`/`RepositoryVerifyResponse`.
- New `src/starrocks_br/s3_verify.py`: `verify_s3_connection(...)`.
- `src/starrocks_br/api/routes/repositories.py`: new `POST /repositories/verify` route.
- New `tests/test_api_s3_verify.py`.
- Not going through StarRocks/`CREATE REPOSITORY` — this is a direct API-server-to-S3 check, explicitly rejected earlier in favor of this approach.
