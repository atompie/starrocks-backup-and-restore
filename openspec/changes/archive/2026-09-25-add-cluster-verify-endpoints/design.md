## Context

`_cluster_connect.py` already centralizes cluster lookup and connection (`get_cluster_or_404`, `connect`, `connect_or_503`). `connect_or_503` is used by `jobs.py` and `repositories.py` and always raises HTTP 503 on a connection failure — those call sites need a working connection to proceed and have nothing useful to do without one. Verification is different: a failed connection *is* the answer the caller wants, not an error condition, so it needs its own helper rather than reusing `connect_or_503`. `StarRocksDB.connect()` (`db.py`) has no timeout today; every existing caller connects to a cluster it already expects to be reachable, so a hang has never mattered enough to add one. Verification exists specifically to catch unreachable hosts, so it needs a bounded wait.

## Goals / Non-Goals

**Goals:**
- One connection-verification code path shared by both new endpoints.
- Bounded-time verification (no indefinite hang on an unreachable host).
- Never let a password reach a response, log line, or raised exception's `str()`.

**Non-Goals:**
- Changing timeout or error-handling behavior for existing `connect`/`connect_or_503` call sites (jobs, repositories) — they keep today's behavior.
- Distinguishing failure causes beyond what the underlying driver error already conveys (e.g. no bespoke "unreachable host" vs. "timeout" taxonomy) — see Decisions.
- Persisting verification results or history.

## Decisions

**New `verify_connection(host, port, user, password, database, *, timeout=5) -> ClusterVerifyResponse` in `_cluster_connect.py`.**
Both routes call this one function; neither route touches `mysql.connector` or `StarRocksDB` directly. It builds a `StarRocksDB`, calls `.connect()` inside try/except, and always closes the connection in a `finally` before returning — this is a one-shot check, not a connection the caller reuses. `connect()`/`connect_or_503()` are untouched, since they serve a different purpose (get a connection to *use*, not to *test*).

**`StarRocksDB` gains an optional `connect_timeout: int | None = None` constructor param**, forwarded into `conn_args["connect_timeout"]` only when set. Existing callers (`connect`, `connect_or_503`) don't pass it, so their behavior is unchanged (no timeout, same as today). `verify_connection` passes `timeout=5`. Alternative considered: setting a global default timeout in `StarRocksDB.connect()` itself — rejected because it would silently change behavior for every existing job/repository connection, which is out of scope and could turn a slow-but-working connection into a spurious failure.

**Error message = sanitized `str(exception)` from the connection attempt.** `mysql.connector.Error` messages (e.g. `1045 (28000): Access denied for user 'x'@'y'`, `2003 (HY000): Can't connect to MySQL server on 'host:port'`, `1049 (42000): Unknown database 'x'`) already distinguish auth failure, unreachable host, and missing database without embedding the password — mysql.connector's own error text does not include the password field. `verify_connection` still defensively checks the message doesn't contain the raw password string before returning it, in case a future driver version changes that. Alternative considered: a fixed enum of categories ("AUTH_FAILED", "UNREACHABLE", ...) with generic messages — rejected as unnecessary complexity; the goal only asks for "a clear error response," and the driver's own text is already clear and specific.

**Both endpoints return HTTP 200 with `{success, message}` for both outcomes** (confirmed with the user). `get_cluster_or_404` still raises 404 for an unknown `cluster_id`, and pydantic validation still raises 422 for a malformed `POST /clusters/verify` body — those are request-level errors, not connection-attempt outcomes, so they keep the codebase's existing HTTPException convention.

**Database check happens for free at connect time.** Passing `database` into `mysql.connector.connect(**conn_args)` makes the driver validate the database exists as part of the handshake (raising `1049 Unknown database` if not) — no separate `USE <db>` or query is needed to satisfy "if database is provided, verify that the database can also be accessed."

## Risks / Trade-offs

- [Driver error text changes wording across `mysql-connector-python` versions] → Tests assert on `success`/general shape, not exact message text; the defensive password-substring check keeps message content safe regardless of wording.
- [5s timeout may be too short on a slow/loaded network] → It's scoped to the verify path only; if it proves too aggressive in practice, it's a one-line change to `verify_connection`'s default, not a schema or spec change.
- [Passing a plaintext password through `ClusterVerifyRequest` in `POST /clusters/verify`] → Same trust boundary as `POST /cluster` (`ClusterCreate` already accepts a plaintext password over the same authenticated API); no new exposure. Not persisted, never echoed back.
