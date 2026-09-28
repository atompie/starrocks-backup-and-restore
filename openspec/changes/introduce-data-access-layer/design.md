# Design

## Context

See `proposal.md` - Why. Current state, per a fresh audit of `src/starrocks_br/` (excluding `store/` and `api/`, which are already clean):

- `db.py`'s `StarRocksDB` is the only StarRocks connection abstraction: `execute(sql: str) -> None`, `query(sql: str, params: tuple = None) -> list[tuple]`, plus a `timezone` property. All callers pass fully-built SQL strings; no caller uses the `params` argument today.
- `utils.py` holds `quote_identifier`, `quote_value`, `build_qualified_table_name` — used consistently everywhere except the two bug sites called out in the proposal.
- `store/` (session.py, models.py) is already a clean SQLAlchemy layer; not touched by this change.
- Modules split into three groups:
  - **Pure StarRocks-SQL, no ORM**: `health.py`, `repository.py` — clean lift-and-shift.
  - **Pure metadata (SQLAlchemy ORM only), already clean**: `labels.py`, `inventory_groups.py`, `history.py`, `commands/jobs.py` — no bugs, just relocation for discoverability.
  - **Mixed** (StarRocks SQL and ORM access interleaved in the same module, sometimes the same function): `planner.py`, `restore.py`, `executor.py`, `concurrency.py`, `prune.py`. These require splitting StarRocks-facing code out from orchestration logic without changing the orchestration itself.

## Goals / Non-Goals

**Goals:**
- One place (`dal/db/`) for all StarRocks SQL/DDL construction and execution, built on the existing `StarRocksDB` driver.
- One place (`dal/metadata/`) for the already-clean SQLAlchemy modules, so new metadata access has an obvious home.
- Fix the three identifier/value-quoting gaps (`prune.py:168`, `prune.py:191`, `executor.py:169`) as part of the extraction, not as a separate patch.
- Preserve all existing function signatures used by callers where practical, changing only *where* the SQL-building code lives, so orchestration modules (`planner.py`, `restore.py`, `executor.py`, `concurrency.py`, `prune.py`, `commands/*`) change their imports/call sites but not their control flow.

**Non-Goals:**
- Not fixing the `commands/backup.py` session-held-open-across-StarRocks-execution gap documented in `AGENTS.md` — separate concern, separate change.
- Not introducing parameterized queries or a query-builder abstraction — `quote_identifier`/`quote_value` remain the quoting mechanism; `dal/db` functions call them internally so callers can't forget.
- Not changing StarRocks command semantics (BACKUP/RESTORE/SHOW/DROP syntax) for any already-correctly-quoted input.
- Not moving `store/` or `api/` — out of scope, already clean.

## Decisions

**1. Two packages, split by target system, not by "layer style."**
`dal/db/` = StarRocks SQL (talks to `StarRocksDB`). `dal/metadata/` = SQLite/SQLAlchemy (talks to `store/session.py`). Alternative considered: a single `dal/` package with mixed-concern submodules per business area (e.g. `dal/backup.py` holding both StarRocks and metadata calls for backups) — rejected because it would recreate the exact mixing this change is meant to eliminate; splitting by target system makes "does this touch StarRocks or SQLite" visible from the import alone.

**2. `dal/db` submodules mirror the existing business-area modules they extract from**, not the StarRocks object type. E.g. `dal/db/backup.py` holds backup-related StarRocks calls extracted from `planner.py`/`executor.py`; `dal/db/restore.py` holds restore-related calls from `restore.py`; `dal/db/repository.py` and `dal/db/health.py` are near-verbatim moves of `repository.py`/`health.py`. Alternative considered: organize by StarRocks statement type (`dal/db/show.py`, `dal/db/backup_ddl.py`) — rejected as it would scatter closely-related calls (e.g. submit + poll for the same operation) across files with no benefit, since callers already think in terms of backup vs. restore vs. repository.

**3. `dal/metadata` gets the four already-clean ORM modules moved as-is** (`labels.py`, `inventory_groups.py`, `history.py`, `commands/jobs.py`'s data-access functions), with call sites updated to import from the new location. Alternative considered: leave them where they are since they have no bugs to fix — rejected because the proposal's goal is a single discoverable home for metadata access; leaving four clean modules out of `dal/metadata` while moving everything StarRocks-related would leave the split inconsistent and half-finished.

**4. Mixed modules keep their orchestration logic in place; only the StarRocks-facing statements move.** For example, `concurrency.reserve_job_slot` continues to live in `concurrency.py` and orchestrate ORM reads/writes and a staleness check, but the staleness check's `SHOW BACKUP FROM`/`SHOW DATABASES` calls move into `dal/db/concurrency.py` (or the relevant `dal/db` submodule) and `reserve_job_slot` calls through it. Alternative considered: fully re-architect these modules into separate command/query objects — rejected as far larger in scope than the proposal (introducing a DAL), and risks changing behavior in code with real concurrency-safety requirements (see `AGENTS.md` on `concurrency.reserve_job_slot`).

**5. Bug fixes ride along with the extraction, not as a separate PR.** `prune.py`'s two unescaped f-strings and `executor.py`'s missing `quote_identifier` are fixed by writing their `dal/db` replacements correctly (using `quote_identifier`/`quote_value` like every other extracted call), rather than patching the bug in place first and then moving the code. Alternative considered: fix the bugs in a separate, smaller PR first — rejected since the fix and the extraction are the same edit (both touch the same three lines), and splitting them would just create a throwaway intermediate commit.

**6. `StarRocksDB` itself does not move or change.** It's the constructor `dal/db` functions take as an argument (mirroring how callers already pass `db`/`driver` through today, e.g. `executor.py`'s `submit_backup_command(db, ...)`), not something to relocate under `dal/db` conceptually, even though the module remains importable from `starrocks_br.db` for now. Alternative considered: rename/move `db.py` to `dal/db/driver.py` — rejected as an unnecessary additional rename with no functional benefit; can be revisited later if desired, but is not required for this change's goals.

## Risks / Trade-offs

- [Risk] Splitting mixed modules could subtly change error handling or ordering (e.g. StarRocks call happens before vs. after an ORM write). → Mitigation: extract statement-building/execution only, never reorder existing calls within a function; unit tests for each mixed module (`tests/unit/service/test_*.py`) must pass unchanged except for import-path assertions.
- [Risk] `prune.py`'s bug fix changes what SQL is actually sent for repository/snapshot names containing quote characters or special characters — any existing test relying on the old unquoted string form will need updating. → Mitigation: update `tests/unit/service/test_prune.py` (or equivalent) assertions to expect quoted identifiers, matching the pattern already used for `test_executor.py`'s `SHOW BACKUP FROM` assertion.
- [Risk] Moving `labels.py`/`inventory_groups.py`/`history.py`/`commands/jobs.py` into `dal/metadata` touches import paths used throughout `commands/` and tests. → Mitigation: do this move last among the "already-clean" group, one module at a time, running `tests/unit` after each to catch missed import updates immediately.
- [Trade-off] Keeping orchestration logic in the original modules (decision 4) means `dal/db` is not a "thin repository" in the classic sense — some `dal/db` functions will be StarRocks-specific business queries (e.g. `dal/db/backup.py`'s partition-listing query), not generic CRUD. Accepted because the alternative (fully separating orchestration from data access) is a larger refactor than this proposal's scope.

## Migration Plan

1. Create `dal/db/` and `dal/metadata/` packages (empty `__init__.py`).
2. Move `health.py` and `repository.py` into `dal/db/` verbatim (no logic change); update their callers' imports.
3. Move `labels.py`, `inventory_groups.py`, `history.py`, and the data-access functions of `commands/jobs.py` into `dal/metadata/`; update callers' imports.
4. Extract `prune.py`'s StarRocks calls into `dal/db/prune.py`, fixing the two unescaped-interpolation bugs in the same edit; update `prune.py` to call through it.
5. Extract `executor.py`'s StarRocks calls into `dal/db/backup.py` (or extend it if already created by planner extraction), fixing the missing `quote_identifier` in the same edit; update `executor.py` to call through it.
6. Extract `planner.py`'s StarRocks calls into `dal/db/backup.py`.
7. Extract `restore.py`'s StarRocks calls into `dal/db/restore.py`.
8. Extract `concurrency.py`'s StarRocks staleness-check calls into `dal/db/concurrency.py` (or the relevant existing `dal/db` submodule), leaving `reserve_job_slot`'s orchestration in `concurrency.py`.
9. Run the full `tests/unit` suite after each step (per-module, not batched at the end) to catch import/behavior regressions immediately.
10. No schema/migration changes; no rollback beyond reverting the relevant commits, since no data or API contract changes.

## Open Questions

None — the module-by-module ownership and bug-fix approach are settled; remaining detail (exact function names in `dal/db`/`dal/metadata`) is left to `tasks.md` and implementation, not a decision that changes scope.
