## 1. Dependencies and project setup

- [x] 1.1 Add `fastapi`, `uvicorn`, `sqlalchemy`, `alembic`, `httpx`, and a cron-expression library (e.g. `croniter`) to `pyproject.toml` dependencies; verify `pip install -e .` succeeds
- [x] 1.2 Add an `api` optional-dependency extra (or keep in core deps per project preference) and update `docs/installation.md` with the new install path; verify docs build/render with no broken links

## 2. Metadata store (SQLAlchemy + Alembic)

- [x] 2.1 Create `store/models.py` with `Cluster`, `Job`, `Schedule` SQLAlchemy models per design.md Decision 2 (MySQL-compatible column types); verify a unit test can create all tables against an in-memory SQLite engine
- [x] 2.2 Create `store/session.py` with an engine/session factory reading `DATABASE_URL` (default local SQLite file path); verify a test opens a session and performs a round-trip insert/read
- [x] 2.3 Create `store/crypto.py` for encrypting/decrypting `Cluster.password` at rest using a server-side key env var; verify a unit test encrypts then decrypts a password and that the stored value differs from plaintext
- [x] 2.4 Set up Alembic (`store/migrations/`) with an initial migration creating all three tables; verify `alembic upgrade head` succeeds against a throwaway SQLite file
- [x] 2.5 Write unit tests for each model's constraints (unique cluster name, FK from Job/Schedule to Cluster) per `api-cluster-registry` and `api-scheduling` specs

## 3. Cluster registry API

- [x] 3.1 Implement `POST /clusters`, `GET /clusters`, `GET /clusters/{id}`, `PATCH /clusters/{id}`, `DELETE /clusters/{id}` in `api/routes/clusters.py`, backed by `store/models.py`; verify against `api-cluster-registry` spec scenarios (create, duplicate name conflict, missing-field validation, list/get excludes password, update, delete blocked by active jobs)
- [x] 3.2 Write request/response Pydantic schemas that never serialize password/password_encrypted fields; verify a test asserts the field is absent from every cluster response payload
- [x] 3.3 Write integration tests for the cluster registry endpoints using FastAPI's TestClient against a temporary SQLite DB

## 4. Authentication

- [x] 4.1 Implement `api/auth.py` as a FastAPI dependency checking `Authorization: Bearer <token>` against an env-configured API key, applied to all routers except health; verify server fails to start with a clear error when the key env var is unset
- [x] 4.2 Implement `GET /health` unauthenticated liveness endpoint; verify it returns 200 without an Authorization header while other endpoints return 401
- [x] 4.3 Write tests for missing token, wrong token, and correct token per `api-authentication` spec scenarios

## 5. Job execution backend abstraction

- [x] 5.1 Define the `JobBackend` protocol and a backend registry in `jobs/backend.py`, configured from `enabled_backends`/`default_backend` server settings; verify a unit test registers two dummy backends and resolves the default vs. an override by name
- [x] 5.2 Implement `jobs/handlers.py`: `JOB_HANDLERS` map for `backup_full`, `backup_incremental`, `restore`, `prune`, each wrapping the existing `planner`/`executor`/`restore`/`prune` core calls exactly as `cli.py` does today, built from a `Cluster` row; verify a unit test (mocking `StarRocksDB`) that a handler builds the same backup command the CLI would for equivalent inputs
- [x] 5.3 Implement `jobs/thread_backend.py`'s `ThreadPoolExecutor`-based backend: creates the `Job` row as PENDING, transitions to RUNNING before invoking the handler, and to SUCCESS/FAILED after, per design.md Decision 3; verify an integration test submits a job against a mock handler and observes the state transitions (Job row is created PENDING by the submission route before `enqueue` is called, per design.md's split of responsibilities; ThreadBackend itself does the RUNNING/SUCCESS/FAILED transitions)
- [x] 5.4 Wire backend resolution into job submission (`request.backend` -> `cluster.default_backend` -> server default), rejecting a disabled backend with 422; verify against `api-job-execution`'s backend-selection scenarios (implemented in `api/routes/jobs.py::submit_job`, exercised by section 7's tests)

## 6. Progress reporting

- [x] 6.1 Add an optional `on_progress: Callable[[dict], None] | None = None` parameter to `executor.poll_backup_status`, parsing `Progress`/`UnfinishedTasks` (or equivalent) columns from `SHOW BACKUP` rows when present, calling it each poll iteration, defaulting to `None` (no behavior change when omitted); verify existing `tests/test_executor.py` still passes unmodified and add a test asserting the callback receives parsed progress when present and `None` when absent
- [x] 6.2 Add the equivalent `on_progress` parameter to `restore.poll_restore_status`; verify existing `tests/test_restore.py` still passes unmodified and add an analogous progress-callback test (one existing test's local mock signature needed a new `on_progress=None` param to match the new call site — no test assertions changed)
- [x] 6.3 Wire `jobs/handlers.py`'s handlers to pass a callback that persists `progress_pct`/`state_detail` onto the `Job` row via a SQLAlchemy session on each poll; verify an integration test observes `GET /jobs/{id}` returning an updated `progress_pct` mid-run when the mocked poll loop reports one
- [x] 6.4 Confirm actual `SHOW BACKUP`/`SHOW RESTORE` progress-related columns against a real StarRocks instance (e.g. the local test instance at `localhost:9030` once a repository/backup exists there) and adjust the column-parsing logic accordingly; verify by running one real full backup end-to-end and inspecting `GET /jobs/{id}` progress values during it (verified against StarRocks 3.5.11 with a real S3-backed repository: `SHOW BACKUP`'s column order — including `UnfinishedTasks` at index 9 and `Progress` at index 10 — matched the assumed layout exactly; `Progress` was empty/blank throughout a real backup rather than numeric, and `state_detail` correctly tracked SNAPSHOTING → UPLOAD_SNAPSHOT → UPLOAD_INFO → FINISHED with `progress_pct` staying `None` throughout — confirming the "no percentage available, show state only" fallback is the common case in practice, not just a defensive edge case. No parsing changes were needed.)

## 7. Job submission and status API

- [x] 7.1 Implement `POST /clusters/{id}/backups/full`, `POST /clusters/{id}/backups/incremental`, `POST /clusters/{id}/restores`, `POST /clusters/{id}/prunes` in `api/routes/jobs.py`, each creating a `Job` row and calling the resolved backend's `enqueue`; verify HTTP 202 + job id/status response and unknown-cluster 404 per `api-job-execution` spec
- [x] 7.2 Implement `GET /jobs/{id}` returning status, timestamps, progress, and error message; verify against the polling scenarios in `api-job-execution` (mid-run progress, no-progress phase, terminal state, unknown job 404)
- [x] 7.3 Write an end-to-end integration test (thread backend, mocked StarRocks calls) covering submit -> poll -> terminal state for a full backup job
- [x] 7.4 Verify parity: run the same inputs through the existing CLI `backup full` command and through the new API job submission against a mocked/test StarRocks target, and assert the same backup command/label is produced (per the "reuses existing behavior unchanged" requirement)

## 8. Scheduling API

- [x] 8.1 Implement `POST /schedules`, `GET /schedules`, `PATCH /schedules/{id}`, `DELETE /schedules/{id}` in `api/routes/schedules.py`; verify against `api-scheduling` create/list/update/delete scenarios, including invalid-cadence 422 and unknown-cluster 404
- [x] 8.2 Implement `next_run_at` computation from a cron expression on create/update using the cron-expression library; verify unit tests for a few representative cadences
- [x] 8.3 Implement `POST /schedules/run-due`: select due enabled schedules, advance `next_run_at` and submit a job via the same internal submission path used by section 7, within one DB transaction per schedule per design.md Decision 5; verify the due/not-due/no-schedules scenarios from `api-scheduling`
- [x] 8.4 Write a concurrency test asserting two near-simultaneous `run-due` calls do not double-submit a job for the same due occurrence (idempotency scenario) (used 5 concurrent calls via FastAPI TestClient threads)

## 9. CLI API-client commands

- [x] 9.1 Add a new `api` Click subcommand group in a new `cli_api/` module, registered alongside the existing `cli` group, reading server URL/API key from flags and/or env vars; verify `starrocks-br api --help` lists cluster/job/schedule subcommands without altering existing `starrocks-br --help` output for current commands
- [x] 9.2 Implement `starrocks-br api cluster add/list/remove`, calling the cluster registry endpoints; verify against a running test API instance (or `httpx` mock) per `cli-api-client` scenarios
- [x] 9.3 Implement `starrocks-br api job submit --type ... --cluster ... [--wait]` and `starrocks-br api job status <id>`, with `--wait` polling until terminal state and exiting non-zero on FAILED; verify the wait/poll scenario
- [x] 9.4 Implement `starrocks-br api schedule run-due`, calling `POST /schedules/run-due` once and exiting non-zero on an unreachable/erroring API; verify both the triggered-jobs and unreachable-API scenarios from `cli-api-client`
- [x] 9.5 Write tests for missing-API-key and server-401 error handling across the new CLI commands

## 10. Server bootstrap and configuration

- [x] 10.1 Implement `api/app.py` FastAPI app assembly (routers, auth dependency, startup checks for required env vars, backend registry construction from config); verify the server fails fast with a clear message when `STARROCKS_BR_API_KEY` or the DB encryption key is missing
- [x] 10.2 Add a `starrocks-br api serve` CLI command (or documented `uvicorn` invocation) to start the server; verify it starts locally and `GET /health` responds 200
- [x] 10.3 Document required environment variables (API key, DB encryption key, `DATABASE_URL`, `enabled_backends`/`default_backend`) in `docs/configuration.md`

## 11. Documentation and end-to-end verification

- [x] 11.1 Add an API usage guide (new `docs/api.md` or a new section in `docs/commands.md`) covering cluster registration, job submission/polling, and scheduling, with example `curl` and CLI invocations
- [x] 11.2 Update `docs/scheduling.md` to present the API-driven schedule + `api schedule run-due` cron entry as the recommended approach, keeping the existing manual-cron example for users who don't run the API
- [x] 11.3 Run a full manual smoke test against the local StarRocks test instance (`localhost:9030`): register it as a cluster, run `init`-equivalent setup, submit a full backup job via the API, poll to completion, submit a restore job, and confirm results match what the equivalent direct CLI commands produce (done against real S3-compatible (RustFS) storage: created a repository, registered the cluster via `api cluster add`, ran `init` against it directly, submitted a full backup via `api job submit`, confirmed the snapshot, `ops.backup_history`, and repository state; mutated a row, submitted a restore via the API, and confirmed the table returned to its backed-up state with a matching `ops.restore_history` record. Also caught and fixed two real bugs along the way: a `CREATE REPOSITORY` trailing-slash path bug in the manual setup (not a tool defect — documented above for future reference) and a genuine API bug where `ClusterCreate`/`ClusterUpdate` rejected an empty password, which StarRocks itself permits (e.g. a passwordless local root) — fixed in `api/schemas.py`, with a regression test added. All test data (repository, S3 objects, ops schema, smoke database) was cleaned up afterward.)
