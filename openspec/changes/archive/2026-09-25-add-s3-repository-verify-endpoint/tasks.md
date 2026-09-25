## 1. Dependency

- [x] 1.1 Add `"boto3>=1.34.0,<2"` to the `api` optional-dependencies group in `pyproject.toml` and verify `.venv/bin/pip install -e ".[api]"` succeeds and `boto3` is importable.

## 2. Schemas

- [x] 2.1 Add `RepositoryVerifyRequest` (`location`, `access_key`, `secret_key`, `endpoint`, `region`) to `src/starrocks_br/api/schemas.py`, near `RepositoryCreate`, and verify it validates the same required-field shape as `RepositoryCreate` minus `name`.
- [x] 2.2 Add `RepositoryVerifyResponse(success: bool, message: str)` to `src/starrocks_br/api/schemas.py`, mirroring `ClusterVerifyResponse`.

## 3. S3 verification module

- [x] 3.1 Create `src/starrocks_br/s3_verify.py` with `verify_s3_connection(location, access_key, secret_key, endpoint, region, *, timeout=5) -> RepositoryVerifyResponse`, following `_cluster_connect.verify_connection`'s never-raise shape.
- [x] 3.2 Parse the bucket name out of `location` (strip `s3://`, take the first path segment) and return a failure `RepositoryVerifyResponse` (not an exception) for a malformed location.
- [x] 3.3 Build the `boto3` S3 client with path-style addressing (`Config(signature_version="s3v4", s3={"addressing_style": "path"}, connect_timeout=timeout, retries={"max_attempts": 1})`) and call `head_bucket(Bucket=bucket)`.
- [x] 3.4 Catch `botocore.exceptions.ClientError` and map its status/error code to a clear message (403 → auth failure, 404 → bucket not found); catch `EndpointConnectionError`/`ConnectTimeoutError` separately (→ "could not reach endpoint"); catch a generic `Exception` fallback.
- [x] 3.5 Redact `secret_key` from any exception message before it reaches the returned `RepositoryVerifyResponse`, matching the cluster-verify redaction pattern in `_cluster_connect.verify_connection`.

## 4. Route

- [x] 4.1 Add `POST /repositories/verify` (top-level, no `cluster_id`) to `src/starrocks_br/api/routes/repositories.py`, `response_model=RepositoryVerifyResponse`, calling `s3_verify.verify_s3_connection(...)` directly, returning HTTP 200 for both success and failure.

## 5. Tests

- [x] 5.1 Create `tests/test_api_s3_verify.py`, mocking `s3_verify.boto3.client` via `monkeypatch`/`unittest.mock`.
- [x] 5.2 Test: successful `head_bucket` → `{success: true}`, HTTP 200.
- [x] 5.3 Test: `ClientError` with 403 → `{success: false, message}` mentioning an auth failure, HTTP 200.
- [x] 5.4 Test: `ClientError` with 404 → `{success: false}` mentioning bucket not found, HTTP 200.
- [x] 5.5 Test: connection error (unreachable endpoint) → `{success: false}` mentioning unreachable, HTTP 200 (not 500).
- [x] 5.6 Test: `secret_key` never appears in the response body, on every failure branch (403, 404, connection error, malformed location).
- [x] 5.7 Test: malformed `location` (not `s3://...`) → `{success: false}` with no unhandled exception, HTTP 200/422.
- [x] 5.8 Run `.venv/bin/pytest tests/test_api_s3_verify.py -v` and verify all tests pass.

## 6. Manual verification

- [x] 6.1 `POST /repositories/verify` with a real S3-compatible payload (`location: "s3://test"`, `endpoint: "http://localhost:9000"`, a live RustFS instance reachable via kubectl port-forward) and confirm a clean `{success, message}` instead of a raw stack trace. (Success-path check with valid credentials deferred by user request; failure-path response below already confirms no raw stack trace.)
- [x] 6.2 Repeat with a deliberately wrong `secret_key` and confirm the 403 path is reported and the secret never appears in the response. Verified: `200 {'success': False, 'message': "Authentication failed for bucket 'test': 403"}`, wrong secret not present in response body.
