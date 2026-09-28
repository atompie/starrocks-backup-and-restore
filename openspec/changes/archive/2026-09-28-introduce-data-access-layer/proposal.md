# Proposal

## Why

SQL and StarRocks command construction is scattered across roughly a dozen modules in `src/starrocks_br/`, each re-implementing its own mix of raw f-string SQL, `db.execute`/`db.query` calls, and SQLAlchemy ORM access. This makes the identifier/value-quoting discipline (`utils.quote_identifier`/`quote_value`) easy to skip by accident — a fresh audit confirms two live gaps: `prune.py`'s `verify_snapshot_exists` and `execute_drop_snapshot` interpolate `repository`/`snapshot_name` into `SHOW SNAPSHOT`/`DROP SNAPSHOT` without quoting (lines 168, 191), and `executor.py`'s `poll_backup_status` builds `SHOW BACKUP FROM {database}` without `quote_identifier` (line 169), unlike every sibling StarRocks call. Centralizing this construction behind a dedicated data access layer closes these gaps at the source and gives future StarRocks/metadata callers one place to get quoting right by default, rather than relying on each module remembering to import `utils` correctly.

## What Changes

- Introduce `src/starrocks_br/dal/db/` for StarRocks-side SQL/DDL construction and execution, built around the existing `StarRocksDB` driver (`src/starrocks_br/db.py`) and the quoting helpers in `utils.py`.
- Introduce `src/starrocks_br/dal/metadata/` as the home for SQLAlchemy-based metadata access, consolidating the modules that are already clean ORM-only code (`labels.py`, `inventory_groups.py`, `history.py`, `commands/jobs.py`) so future metadata access has one obvious home instead of being spread across the top-level package.
- Move StarRocks-only modules with no metadata dependency (`health.py`, `repository.py`) into `dal/db/` first, since they require no logic changes, only relocation.
- Extract the StarRocks-facing halves of the mixed modules (`planner.py`, `restore.py`, `executor.py`, `concurrency.py`, `prune.py`) into `dal/db/` functions, leaving each module's orchestration/business logic in place but calling through the new layer instead of building SQL inline.
- Fix the two unescaped-interpolation bugs found in `prune.py` (`verify_snapshot_exists`, `execute_drop_snapshot`) and the missing `quote_identifier` in `executor.py`'s `poll_backup_status`, as part of moving that code into `dal/db` (the extraction is the natural point to apply the same quoting convention used everywhere else).
- No change to externally observable behavior for any correctly-quoted identifier/value; the bug fixes only affect inputs that were previously vulnerable to breaking or malformed SQL.

## Capabilities

### New Capabilities

_None._ This is an internal structural refactor of existing modules; it does not add, remove, or change any documented API capability's requirements.

### Modified Capabilities

_None._ Public API behavior, request/response contracts, and StarRocks command semantics for well-formed inputs are unchanged.

## Impact

- **Affected code**: `src/starrocks_br/db.py`, `utils.py`, `health.py`, `repository.py`, `planner.py`, `restore.py`, `executor.py`, `concurrency.py`, `prune.py`, `labels.py`, `inventory_groups.py`, `history.py`, `commands/jobs.py`, and their corresponding tests under `tests/unit/crud/` and `tests/unit/service/`.
- **Not affected**: `src/starrocks_br/store/` (already a clean SQLAlchemy session/model layer), `src/starrocks_br/api/` (routes only call through `commands/`, no direct SQL), `commands/backup.py`, `commands/restore.py`, `commands/prune.py` (orchestration only; may see import changes but no behavior changes).
- **Dependencies**: none added; no schema/migration changes.
- **Related but out of scope**: `commands/backup.py` holding a metadata session open while `executor.execute_backup` submits/polls StarRocks (documented in `AGENTS.md` as a known gap) is a separate architectural issue and is not fixed by this change.
