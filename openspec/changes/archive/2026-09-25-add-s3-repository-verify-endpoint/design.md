## Context

See proposal.md - Why. This is a standalone S3 connectivity/credential check, not routed through StarRocks — that `CREATE REPOSITORY`-based approach was explicitly rejected in favor of checking directly from the API server, since the failure mode being fixed (Docker networking: the StarRocks container couldn't reach `localhost:9000` on the host, while the API host could) is specific to where the check runs from.

The codebase already has one "always return a result, never raise" verification pattern to follow: `_cluster_connect.verify_connection` (`src/starrocks_br/api/routes/_cluster_connect.py:54-89`), used by `POST /clusters/verify`. It builds a real connection, catches the failure, redacts the secret from the exception message via string replace, and returns a `ClusterVerifyResponse` — never a 503/500. This change follows the same shape for S3.

Path-style addressing must match `repository.build_create_s3_repository_command`'s `aws.s3.enable_path_style_access: true` (`src/starrocks_br/repository.py:124`), since this check exists specifically to be tried before that call, against the same MinIO/S3-compatible endpoints.

## Goals / Non-Goals

**Goals:**
- Distinguish, in one request, between: reachable+authorized, reachable+bad credentials, reachable+bucket missing, and unreachable endpoint.
- Never surface a raw exception/500 to the client for any of those outcomes.
- Never leak `secret_key` into the response, under any outcome.

**Non-Goals:**
- Not validating write access, only read/HEAD-level authorization (`head_bucket`) — a bucket where the credentials can `HEAD` but not `PUT` will report success here and could still fail at actual backup time. Acceptable: this check answers "is the endpoint reachable and are these credentials valid," not "can a backup run succeed."
- Not persisting or logging the submitted credentials anywhere.
- Not scoping the check to a registered cluster; no cluster lookup, no DB write.

## Decisions

**boto3 `head_bucket` over a raw HTTP probe.** A hand-rolled SigV4-signed HTTP request would avoid the new dependency, but `head_bucket` already does correct request signing, path-style addressing, and structured error codes (`ClientError.response["Error"]["Code"]` / HTTP status) — reimplementing that is unnecessary risk for a verification-only endpoint. `boto3` becomes a new `api`-extra dependency, matching how other API-only libraries (`fastapi`, `sqlalchemy`) are scoped there rather than in core `dependencies`.

**`head_bucket` over `list_objects`/`put_object`.** `head_bucket` is the minimal-privilege, no-data-transfer check that still round-trips through both authentication and bucket-existence — it directly answers "does this bucket exist and am I authorized," which is exactly what's being verified, without requiring `s3:ListBucket` or `s3:PutObject` permissions the caller's credentials may deliberately lack.

**Separate `s3_verify.py` module, mirroring `_cluster_connect.verify_connection`'s signature style** (return a response object, never raise) rather than adding a third connection helper into `_cluster_connect.py`. `_cluster_connect.py`'s own docstring says it holds cluster-lookup/connection helpers extracted from route modules; an S3-client helper with no `Cluster`/DB dependency doesn't belong there.

**Single retry attempt, 5s connect timeout** (`retries={"max_attempts": 1}`, `connect_timeout=timeout`), matching `_cluster_connect.VERIFY_CONNECT_TIMEOUT_SECONDS`'s default — this is an interactive verification call, not a resilient background job; a hung or retrying client makes the caller wait longer for no benefit.

**No `/cluster/{cluster_id}/...` scoping.** Registered clusters have no relationship to arbitrary S3 credentials being verified before a repository even exists; requiring a cluster id would force the caller to register a cluster first, which the check doesn't need.

## Risks / Trade-offs

- [Read-only check can't catch write-permission-only failures] → Documented as a Non-Goal; the check's purpose is endpoint reachability and credential validity, and backup creation will still surface its own errors if write access is missing.
- [New dependency (`boto3`) adds install size to the `api` extra] → Scoped to the `api` optional-dependencies group only, consistent with other API-only libraries; core CLI installs are unaffected.
- [Error-message text from `botocore` could change across versions and break message-content assumptions in tests] → Tests assert on the *mapped* category (auth failure / not found / unreachable) via mocked `ClientError`/`EndpointConnectionError`, not on verbatim upstream wording.
