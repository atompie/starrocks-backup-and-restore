## Why

The tool has three front doors today — the legacy YAML-driven CLI (`cli.py`), the FastAPI server (`api/routes/*.py`), and an API-client CLI (`cli_api/*`) — and application logic is split unevenly across them. `cli.py`'s `backup full`, `backup incremental`, `restore`, and `prune` commands hand-roll the exact orchestration sequence (health check → ensure repository → plan → reserve job slot → execute) that `jobs/handlers.py` already encapsulates for the API's async job backend, so the same use case exists as two independent implementations that can silently drift (the project even carries a dedicated `test_api_cli_parity.py` to guard against that drift, rather than making drift impossible). Separately, several FastAPI routes (`clusters.py`, `repositories.py`, `schedules.py`) embed real business rules — delete guards, snapshot-retention checks, idempotent cron-advance logic — directly in the route body and express their outcome only as `HTTPException`, so that logic cannot be invoked or tested without going through HTTP.

This change consolidates each application operation into a single implementation in a proper command layer, so `cli.py` and the API both call the same code, and removes the parity test that exists only to compensate for the duplication.

## What Changes

- Promote `jobs/handlers.py` into a `commands/` package (one module per use case, matching the project's flat top-level module convention) that is the single implementation of `backup_full`, `backup_incremental`, `restore`, and `prune`.
- Add command functions for the application-level logic currently embedded in FastAPI routes: cluster deletion guards, repository create/delete rules, and schedule `run_due` (idempotent cron advance + job dispatch), each in `commands/`.
- Introduce domain exceptions (extending `exceptions.StarRocksBRError`) for every business outcome that routes currently express via inline `HTTPException`: cluster has an active job / enabled schedule, repository already exists / still holds snapshots, invalid cadence expression. Extend the existing `SnapshotNotFoundError`-style pattern with a `SnapshotAlreadyExistsError` so the backup command layer stops returning an ad hoc `error_details` dict and raises instead.
- Rewrite `cli.py`'s `backup`, `restore`, `prune` commands to call the shared commands instead of re-implementing the flow; `cli.py` keeps its config loading, cluster upsert-from-YAML, prompts, `sys.exit`, and message formatting (including the `_handle_snapshot_exists_error` retry guidance) as adapter-only code, driven off the same domain exceptions the API now raises.
- Rewrite the affected FastAPI route bodies (`clusters.py::delete_cluster`, `repositories.py::create_repository`/`delete_repository`, `schedules.py::run_due` and its cadence validation) to call the new commands and translate the new domain exceptions to the same `HTTPException` status codes/messages they already return today — no change to any HTTP contract.
- Re-point `tests/test_cli_backup.py`, `tests/test_cli_restore.py`, `tests/test_prune_cli.py`, `tests/test_jobs_handlers.py`, `tests/test_api_clusters.py`, `tests/test_api_repositories.py`, and `tests/test_api_schedules.py` so application-behavior assertions run against the commands directly; narrow the CLI/API tests that remain to adapter concerns (argument parsing/output formatting for CLI, request validation/status-code translation for API).
- Delete `tests/test_api_cli_parity.py` — the property it checks (CLI and API agree) becomes structurally guaranteed once both call the same command, so the test has nothing left to prove.
- **Out of scope**: `cli_api/*` (the `starrocks-br api ...` HTTP-client CLI) is unchanged. It is a genuine remote client of a separately-running API server, not a duplicate implementation of any use case, so it stays a thin HTTP adapter exactly as it is today.

No CLI flag, output message, exit code, HTTP status code, or response body changes as a result of this refactor, **except one disclosed behavior change**: `prune`'s per-snapshot delete-failure policy. Today, `cli.py`'s `prune` command continues attempting remaining snapshots in a batch after one fails (partial success, reported per-snapshot), while `jobs/handlers.py::run_prune` (the job/API path) aborts the whole batch on the first failure. Consolidating these into one `commands/prune.py::run_prune` requires picking one policy; per user decision, the unified command **aborts on first failure** (matching today's job/API behavior). This changes `cli.py prune`'s behavior for a multi-snapshot batch where a delete fails partway through: it now stops and reports failure instead of continuing to delete the remaining snapshots. `tests/test_prune_cli.py::test_prune_partial_failure_continues_deletion` is rewritten to `test_prune_batch_aborts_on_first_failure` (or similar) to assert the new abort-on-first-failure behavior instead of continue-past-failure.

## Capabilities

### New Capabilities
(none — this change restructures existing internal implementation; it introduces no new externally observable capability)

### Modified Capabilities
(none — see below)

This change is a pure internal refactor: no HTTP contract, CLI interface, or observable behavior changes. Per the instructions on `skip_specs`, no capability requirements change, so this change sets `skip_specs: true` in `.openspec.yaml` and carries no delta spec files.

## Impact

- **Code**: new `src/starrocks_br/commands/` package (replacing `src/starrocks_br/jobs/handlers.py`, whose module is renamed/split); `src/starrocks_br/exceptions.py` (new domain exceptions); `src/starrocks_br/cli.py` (backup/restore/prune bodies rewritten as adapters); `src/starrocks_br/api/routes/clusters.py`, `repositories.py`, `schedules.py` (rewritten as adapters); `src/starrocks_br/jobs/backend.py`/`thread_backend.py` (import path update only, same call contract).
- **Tests**: `tests/test_jobs_handlers.py` relocates/renames to test the `commands/` package directly; `tests/test_cli_backup.py`, `test_cli_restore.py`, `test_prune_cli.py`, `test_api_clusters.py`, `test_api_repositories.py`, `test_api_schedules.py` narrow to adapter-only assertions; `tests/test_api_cli_parity.py` is deleted.
- **No dependency, schema, or migration changes.**
- **No change** to `cli_api/*` or its tests (`test_cli_api_client.py`).
