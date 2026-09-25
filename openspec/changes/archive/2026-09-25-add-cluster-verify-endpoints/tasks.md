## 1. Connection layer

- [x] 1.1 Add optional `connect_timeout: int | None = None` to `StarRocksDB.__init__`/`connect()` in `src/starrocks_br/db.py`, forwarded into `conn_args` only when set; verify existing callers (`connect`, `connect_or_503`) still pass no timeout and existing db tests still pass
- [x] 1.2 Add `verify_connection(host, port, user, password, database, *, timeout=5)` to `src/starrocks_br/api/routes/_cluster_connect.py`: build a `StarRocksDB`, attempt `.connect()` in try/except, always close in `finally`, return a `ClusterVerifyResponse`-shaped result (success + sanitized message); verify a unit test covers both the success and failure branches without hitting a real cluster

## 2. Schemas

- [x] 2.1 Add `ClusterVerifyRequest` (host, port default 9030, user, password default "", database: str | None = None) and `ClusterVerifyResponse` (success: bool, message: str) to `src/starrocks_br/api/schemas.py`; verify `password` never appears in `ClusterVerifyResponse`'s fields

## 3. Endpoints

- [x] 3.1 Add `POST /clusters/verify` on `clusters_router` in `src/starrocks_br/api/routes/clusters.py`: validate via `ClusterVerifyRequest`, call `verify_connection(...)`, return `ClusterVerifyResponse` with HTTP 200 for both success and failure, no cluster created or modified; verify with a request against a mocked successful connection
- [x] 3.2 Add `GET /cluster/{cluster_id}/verify` on `cluster_router`: `get_cluster_or_404`, decrypt stored password, call `verify_connection(...)` with the cluster's stored fields, return `ClusterVerifyResponse` with HTTP 200 for both success and failure, unknown `cluster_id` still 404; verify with a request against a mocked failing connection and a request for a nonexistent cluster id
- [x] 3.3 Handle `decrypt_password` failure (rotated/wrong encryption key) in `GET /cluster/{cluster_id}/verify` as a graceful `success: false` result instead of an unhandled 500; found via manual smoke test against a cluster registered under an older encryption key

## 4. Tests

- [x] 4.1 Add tests to `tests/test_api_clusters.py` for `POST /clusters/verify`: successful connection (mocked), failed connection (mocked), and `database = null` (mocked, asserting no database is passed to the connection attempt); verify all three pass
- [x] 4.2 Add tests to `tests/test_api_clusters.py` for `GET /cluster/{cluster_id}/verify`: successful connection against a registered cluster (mocked), failed connection (mocked), `database = null` on a registered cluster (mocked), and unknown `cluster_id` returns 404; verify all four pass
- [x] 4.3 Add a test asserting the stored/submitted password never appears in a verify response body, including on failure; verify it passes

## 5. Documentation

- [x] 5.1 Confirm generated OpenAPI docs (`/openapi.json` or the FastAPI docs UI) show both new paths with `ClusterVerifyRequest`/`ClusterVerifyResponse` schemas and no password value in any example; adjust `Field` descriptions/examples in schemas.py if needed
