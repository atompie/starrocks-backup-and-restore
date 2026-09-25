## Context

See proposal.md for motivation. Relevant current state (verified against the codebase, not just TASK_4.md's claims):

- The SQLite metastore (`store/models.py`, Alembic-migrated) already holds `Cluster`/`Job`/`Schedule`, using surrogate integer PKs and plain `ForeignKey("clusters.id")` references — but **no `ondelete="CASCADE"` and no multi-column `UniqueConstraint` exist anywhere today**; both are new patterns this change introduces.
- `store/session.py` has zero SQLite-specific connection handling today (no `PRAGMA` pragmas, no `event.listens_for` hooks). `session_scope()` (a commit/rollback/close contextmanager) already exists and is used elsewhere.
- The 5 ops tables today live in a StarRocks database named via `Cluster.ops_database`, built by `schema.py`'s DDL string builders. Their column lists are used as-is in the new SQLAlchemy models (verified 1:1 match, no changes needed to the shape of the data).
- Every function that touches an ops table is threaded through as `f(db, ..., ops_database="ops")` where `db` is an untyped StarRocks connection wrapper (`.query()`/`.execute()`/`.timezone`). Some functions are ops-only (pure candidates for a `(session, cluster_id)` conversion); others interleave ops-table access with live StarRocks calls (`SHOW TABLES`, `SHOW PARTITIONS`, `SHOW BACKUP`) and must keep both `db` and gain `session`/`cluster_id`.
- `jobs/handlers.py`'s `run_*` handlers currently take `(cluster: Cluster, params: dict, on_progress=None)` with **no SQLite session at all** — `jobs/thread_backend.py::_run_job` deliberately `expunge()`s `cluster`/`job` from its session before invoking the handler, so today's architecture already avoids holding a session across a long-running handler call.
- `cli.py` is a fully separate, config-YAML-driven code path with **no cluster-identity concept whatsoever** — it builds its own StarRocks connection directly from YAML and has never touched the SQLite metastore.
- `prune.py` has a real pre-existing SQL-injection-shaped bug: unlike every other ops module, it interpolates `repository`/`group`/`snapshot_label` into SQL via raw f-strings with no escaping (worst instance: `execute_drop_snapshot`'s unquoted `repository` spliced into a destructive `DROP SNAPSHOT` statement).

## Goals / Non-Goals

**Goals:**
- Move all 5 ops tables into the SQLite metastore, scoped by `cluster_id`, with `pytest tests/` staying green after every incremental step.
- Remove `ops_database` from every user-facing surface (API schemas, CLI flags, YAML config).
- Keep both code paths (API/jobs and legacy `cli.py`) working against the same shared SQLite tables.
- Make the ops-table access pattern inside job handlers safe under future concurrent job execution (see Decisions, below) — this tool is expected to eventually run backups from a Kafka-consumer-based worker where many jobs can execute truly concurrently across processes/threads, not just one at a time on a single thread-pool backend.

**Non-Goals:**
- Data migration — no production data exists yet; this is a clean cutover, not a dual-write or backfill.
- Fixing `prune.py`'s `verify_snapshot_exists`/`execute_drop_snapshot` unquoted-interpolation bug — these stay StarRocks-only and unconverted; the same bug in the ops-table queries of `get_successful_backups`/`cleanup_backup_history` is fixed only as an incidental side effect of the ORM conversion, not as a deliberate security fix in scope.
- Adding a real foreign key from `backup_partitions.label` to `backup_history.label` — StarRocks today only has a comment-based (unenforced) relationship, and adding real referential integrity now would actually break the existing call order (`record_backup_partitions` runs before `history.log_backup` inserts the parent row in `run_backup_full`/`run_backup_incremental`).
- Building the future Kafka-consumer job backend itself — this design only ensures the SQLite access pattern introduced here won't need rework when that backend arrives.

## Decisions

**1. Per-function signature conversion pattern.** `f(db, ..., ops_database="ops")` becomes `f(session: Session, cluster_id: int, ...)` for functions that only touch ops tables, dropping `db` entirely. A function that mixes ops-table access with a live StarRocks call keeps `db` **and** gains `session`/`cluster_id` (e.g. `planner.find_recent_partitions`, `restore.execute_restore`, `executor.execute_backup`). This mirrors the existing convention used for `Cluster`/`Job`/`Schedule` ORM access elsewhere in the codebase, and keeps StarRocks-only functions (`get_all_partitions_for_tables`, `verify_snapshot_exists`, etc.) completely untouched.

**2. Session lifetime inside job handlers — short-lived, per-touchpoint sessions, not one held open per handler call.**
Alternatives considered:
- *One `session_scope()` held open for the entire handler call* (initially the natural choice, since only one handler runs per job today and it mirrors the existing `with database:` nesting style). **Rejected**: SQLite allows exactly one writer at a time regardless of journal mode — WAL only lets readers coexist with an in-progress writer, it does not add concurrent writers. A future Kafka-consumer-based job backend is expected to run many backups concurrently across workers/processes. If each handler held one write session open for its entire multi-minute StarRocks submit-and-poll duration, every other concurrent job's ops-table access (its own `reserve_job_slot`, `log_backup`, progress updates) would queue behind it for minutes — an availability problem, not just a performance one.
- *Per-access session factory threaded through every signature* — rejected as unnecessary indirection once the chosen approach (below) achieves the same effect without changing any handler's parameter list.
- **Chosen: each handler opens a fresh, narrowly-scoped `with session_scope() as session:` block only around the specific moment it needs to read/write ops tables** — e.g. one brief block around `concurrency.reserve_job_slot`/planner ops-reads before submitting to StarRocks, and a separate brief block around `history.log_backup` + `concurrency.complete_job_slot` after polling completes. The StarRocks submit/poll loop itself is never wrapped in an open session. This is also more faithful to today's actual behavior than the single-session alternative: ops writes against StarRocks already happen as discrete statements, not one transaction spanning the whole job.
- **Consequence**: `run_*` handler signatures do **not** need a new `session` parameter — `cluster.id` (already available on the passed `Cluster` object) is enough; handlers import `session_scope` from `store.session` and manage it themselves. `jobs/thread_backend.py::_run_job`'s existing `expunge()` pattern needs **no change** — it already keeps the handler decoupled from any long-lived session, which turns out to be the correct shape rather than a problem to fix.
- `PRAGMA journal_mode=WAL` is still enabled (alongside `PRAGMA foreign_keys=ON`) for the unrelated benefit of letting API-route reads proceed concurrently with a job's brief writes; it does not by itself solve concurrent-writer serialization, which is why the short-lived-session design above is the actual mitigation for the stated future-concurrency goal.

**3. `PRAGMA foreign_keys=ON` via `event.listens_for(Engine, "connect")`.** Needed for `ondelete="CASCADE"` to actually take effect on SQLite, which does not enforce foreign keys by default. Registered once at module import time in `store/session.py`, guarded to SQLite connections only.

**4. Legacy `cli.py` gains a derived cluster identity, not a new required config field.** `get_cluster_identity(config)` returns `config["name"]` if a new *optional* YAML key is present, else derives `f"{host}:{port}/{database}"` from fields that are already required. `resolve_cluster(session, cfg)` gets-or-creates a `Cluster` row by that identity, refreshing mutable connection fields from YAML on each run (but never overwriting the stored identity itself once created, unless `config["name"]` is explicitly present and differs). `init` is the only command allowed to create the row; every other command requires it to already exist and fails with a "run init first" error otherwise — preserving today's stricter must-init-first behavior and guarding against `--config` typos silently registering phantom clusters.

**5. `schema.py` deletion is sequenced, not immediate.** It can't be deleted until every importer (ultimately `jobs/handlers.py` and `cli.py`) has been converted, since both still reference it (`ensure_ops_schema`, `initialize_ops_schema`, `bootstrap_table_inventory`) until their respective phases land. `bootstrap_table_inventory`'s logic relocates into `inventory_groups.py`, its natural new home.

## Risks / Trade-offs

- **[Risk]** Ops-table writes now commit at each short-lived-session boundary rather than atomically with the StarRocks operation they describe (e.g. a crash between the StarRocks poll completing and the `history.log_backup` session committing loses that history row, same as today's already-fire-and-forget `try/except: pass` around these calls in `executor.execute_backup`). → **Mitigation**: this is not a regression — today's StarRocks-side ops writes are already best-effort, wrapped in bare or logged exception handlers that don't fail the overall job; the new design preserves that same failure-tolerance boundary, just against SQLite instead of StarRocks.
- **[Risk]** `alembic upgrade head` becomes a hard prerequisite for both code paths, replacing zero-setup auto-initialization. → **Mitigation**: document prominently in CHANGELOG and CLI `--help`/first-run messaging; this is an intentional, disclosed breaking change, not an oversight.
- **[Risk]** `concurrency._can_heal_stale_job`'s parameter order (`db` currently 3rd positional, unlike every other function in the file) is an easy place to introduce a silent argument-order bug when adding `session`/`cluster_id`. → **Mitigation**: normalize `db` to first position in the same commit that updates its sole caller (`reserve_job_slot`), since all call sites are touched in this change anyway.
- **[Trade-off]** `backup_partitions.label` remains without a real FK to `backup_history.label` even though SQLite now makes one possible. → Accepted as out of scope (see Non-Goals); revisit separately if referential integrity there becomes valuable later.

## Migration Plan

Implemented as an ordered sequence of code changes with no live data to migrate (greenfield tables). See tasks.md for the full ordered checklist. High-level shape:
1. Additive: new SQLAlchemy models, Alembic migration, SQLite pragmas — nothing references the new tables yet, so this cannot break existing behavior.
2. Convert ops-only modules (`history.py`, `labels.py`, `concurrency.py`, `inventory_groups.py`), then mixed-dependency modules (`planner.py`, `prune.py`, `restore.py`, `executor.py`), each with their tests updated in lockstep.
3. Rewrite `error_handler.py`'s StarRocks-SQL-hint messages.
4. Wire `jobs/handlers.py` + confirm `jobs/thread_backend.py` needs no change (per Decision 2).
5. Simplify FastAPI routes and schemas; remove the CLI flag; give `cli.py` its own identity-resolution flow.
6. Delete `schema.py` and its dedicated test file once all importers are converted; add new ops-table tests (uniqueness constraints, cascade delete) to `test_store_models.py`.
7. Update docs/CHANGELOG.

**Rollback**: since there's no production data, rollback is a plain revert of the commits plus `alembic downgrade` to the prior migration head — no data-preservation concern.

## Open Questions

None — the two decisions that would have changed the approach (session lifetime, backup_partitions FK) are resolved above.
