## Why

Registering a cluster with `POST /cluster` stores connection details, but there is no way to confirm those details actually work — a typo'd host, a wrong port, or a stale password isn't discovered until a real backup/restore job fails against it. Operators need a way to test connectivity both before registration (to validate what they're about to submit) and after (to check whether stored credentials still work).

## What Changes

- Add `POST /clusters/verify`: accepts raw connection parameters (host, port, user, password, database), attempts a real connection, and reports success/failure. Does not create or modify a cluster.
- Add `GET /cluster/{cluster_id}/verify`: loads a registered cluster's stored connection details and attempts the same real connection check. Does not modify the cluster record.
- Both endpoints share one new connection-verification helper in `_cluster_connect.py` — no duplicated connection logic between the two routes.
- Both endpoints return `{"success": bool, "message": str}` with HTTP 200 for both outcomes (a connection failure is not itself a 4xx/5xx — the boolean carries the result). Unknown `cluster_id` still returns 404; an invalid request body still returns 422. This is a deliberate difference from the existing `connect_or_503` helper (still used unchanged by jobs/repositories), which raises 503 on failure.
- Add an optional connect timeout (5s default) used only on the verify path, so an unreachable host fails fast instead of hanging.
- Never expose the plaintext or encrypted password in verify responses, logs, exceptions, or OpenAPI examples.

## Capabilities

### Modified Capabilities
- `api-cluster-registry`: adds a new requirement for verifying a cluster's connection, both for ad hoc connection parameters and for an already-registered cluster.

## Impact

- `src/starrocks_br/api/routes/_cluster_connect.py`: new `verify_connection(...)` helper reused by both endpoints.
- `src/starrocks_br/api/routes/clusters.py`: two new routes (`POST /clusters/verify` on `clusters_router`, `GET /cluster/{cluster_id}/verify` on `cluster_router`).
- `src/starrocks_br/api/schemas.py`: new `ClusterVerifyRequest` / `ClusterVerifyResponse` models.
- `src/starrocks_br/db.py`: `StarRocksDB` gains an optional `connect_timeout`, used only by the verify path; existing callers (`jobs.py`, `repositories.py` via `connect`/`connect_or_503`) are unaffected.
- `tests/test_api_clusters.py`: new tests for success, failure, and `database = null` on both endpoints.
- OpenAPI docs: new paths and schemas, no password in examples.
