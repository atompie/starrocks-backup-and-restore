## Context

See proposal.md - Why. Relevant existing pieces:

- `tests/integration/test_full_backup_restore_cycle.py` already implements a manual
  full-backup-then-restore cycle against a real StarRocks + S3 stack, with helpers
  (`it_names`, `seeded_database`, `_table_ddl`, `_wait_for_job`) private to that file.
- `POST /backup/schedules/cluster/{id}` always computes `next_run_at` as the next cron
  occurrence strictly after creation time (`commands/schedules.py::compute_next_run_at`);
  there is no "run now" option in the schema.
- A scheduled job's params never include a `name` (`ScheduleCreate` has no such field), so
  `labels.determine_backup_label` auto-generates a date-based label. Nothing in the API
  surface (`JobRead`, schedule endpoints) exposes that generated label back to the caller.
- The API server's metadata store is a per-test SQLite file reachable via
  `starrocks_br.store.session.get_engine()`, the same engine `api_client` and the app share
  (see `tests/conftest.py::api_client`/`api_env`).

## Goals / Non-Goals

**Goals:**
- Exercise the schedule -> run-due -> job execution -> restore path against real
  StarRocks/S3 infrastructure, the same way the manual path is already covered.
- Keep the test deterministic and no slower than necessary (avoid real clock waits).
- Avoid duplicating setup/teardown fixtures between the two integration test files.

**Non-Goals:**
- No production code or API changes (no new endpoint to expose job results or a
  "run now" schedule option).
- No coverage of incremental scheduled backups, disabled schedules, or multiple
  concurrent due schedules - this test is a single happy-path cycle.

## Decisions

**1. Force the schedule due by backdating `next_run_at` directly, not by waiting on a
real cadence.**
Alternative considered: cadence `"* * * * *"` plus sleeping until the next minute
boundary. Rejected because it adds up to ~60s to the test and is flaky near boundaries.
Instead, after creating the schedule through the API, open a session against the same
`get_engine()` the API server uses and set that schedule's `next_run_at` into the past,
then call `POST /backup/schedules/run` as normal. This mirrors how `sr_admin_db` already
reaches past the API for StarRocks-side setup/teardown - reaching past the API for the
metadata store in a test is the same kind of test-only shortcut, not a production change.

**2. Discover the generated backup label via a direct metadata-store read.**
Alternative considered: extend `JobRead`/add an endpoint to expose job result payloads.
Rejected as out of scope - it's a real gap but a separate, production-facing change with
its own spec impact; this change only adds test coverage. Instead, after the triggered
job reaches `SUCCESS`, query `BackupHistory` (or the job's `result_json`) directly via a
session against the shared test engine to read the label the scheduled backup produced,
then pass it as `target_label` to the existing manual restore endpoint.

**3. Extract shared fixtures into `tests/integration/conftest.py`.**
`it_names`, `seeded_database`, `_table_ddl`, and `_wait_for_job` currently live in
`test_full_backup_restore_cycle.py`. Both integration tests need identical cluster/
database/table setup and job-polling, so move these into `tests/integration/conftest.py`
(matching the existing convention that shared fixtures live at the relevant conftest
root) rather than duplicating them in the new file.

## Risks / Trade-offs

- [Backdating `next_run_at` bypasses the API's own validation/creation path for that
  field] -> Mitigation: the schedule is still created entirely through the API first;
  only the single timestamp field is adjusted directly, and the run-due call itself goes
  through the real endpoint, so the behavior under test (run-due picking up a due
  schedule and submitting a job) is unaffected.
- [Reading `BackupHistory`/`result_json` directly couples the test to internal storage
  shape] -> Mitigation: this is test-only, isolated to one helper function; if the
  storage shape changes, only that helper needs updating, not the test's assertions
  about restored data.
