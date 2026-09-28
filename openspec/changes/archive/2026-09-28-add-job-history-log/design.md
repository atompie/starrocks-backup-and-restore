## Context

See `proposal.md` - Why. Today's `backup_history`/`restore_history` tables (`store/models.py:146-177`)
are one best-effort summary row per run keyed by `(cluster_id, label)`, written once at the end
inside a bare `try/except: pass` (`executor.py:289-305`, `restore.py:301-312`). Progress is already
observed per-poll — `executor.poll_backup_status` / `restore.poll_restore_status` already call an
`on_progress` callback with StarRocks' own `SHOW BACKUP`/`SHOW RESTORE` `State` value on every
state change (`executor.py:204-217`) — but nothing durable captures those intermediate values
today. AGENTS.md requires metadata writes to use short-lived sessions and never hold a transaction
open across a StarRocks operation; a poll-driven append is exactly the shape that constraint is
written for.

**Discovered during implementation:** `backup_history` is not only this best-effort summary row —
it is the system's only backup catalog, consulted by four modules, not just restore:
- `restore.find_restore_pair`/`find_backup_repository` (via `commands/restore.py`): resolve a
  restore's backup lineage (which full backup precedes a given incremental) and its repository.
- `labels.determine_backup_label`: checks existing labels on the cluster for uniqueness.
- `planner.find_latest_full_backup`/`find_recent_partitions`: resolve the baseline full backup an
  incremental backup diffs against.
- `prune.get_successful_backups`/`cleanup_backup_history`: list prunable snapshots (joined with
  `BackupPartition`/`TableInventory`) and delete the catalog row once a snapshot is pruned.

None of `label`, `backup_type`, `repository`, `finished_at` exist in the append-only log shape this
change moves `backup_history` to, and `cleanup_backup_history`'s delete is fundamentally
incompatible with the new table being an immutable, never-deleted execution log. See Decision below
for the resolution, which extends to all four call sites, not just restore.

## Goals / Non-Goals

**Goals:**
- Turn `backup_history`/`restore_history` into genuine append-only logs, one row per distinct
  StarRocks-reported state plus a final `SUCCESS`/`FAILED` row, linked to `Job.id`.
- Keep row volume small: no row is written when the state is unchanged from the job's last
  recorded row.
- Each insert is its own short-lived session (no long-lived transaction spanning the poll loop).

**Non-Goals:**
- Retention Jobs are not logged here (`Job.status` alone covers them) — see `PLAN.md` §10.
- No backfill/migration of existing `backup_history`/`restore_history` rows into the new shape;
  historical rows have no `job_id` to attach to and are dropped (see Migration Plan).
- `Job` gains exactly two columns (`label`, `repository`) to carry restore-lineage lookups that
  `backup_history` can no longer serve (see Decisions below); no other change to `Job`'s shape or
  its role as the current-state row.
- Multi-database backup references, per-table granularity, and the `backup_references` table
  (`PLAN.md` §6) are out of scope; this change only logs job-level state transitions.

## Decisions

**One log table per job kind, not a shared `job_events` table.** `PLAN.md` originally proposed a
single `job_events` table for all job types. Reusing/repurposing the existing, differently-shaped
`backup_history`/`restore_history` tables was chosen instead, both because they already exist as
the domain-named concepts in `SPEC.md` §17/§28 ("Backup History", "Restore History"), and because
splitting by job kind avoids a `job_type` discriminator column and keeps each table's columns
meaningful without nullable job-kind-specific fields. Retention Jobs get no table at all rather
than an empty `retention_history` — there's nothing to log for them per the current decision.

**`status` holds the StarRocks-native state string, not `Job.status`'s 4-value vocabulary.**
`Job.status` (`PENDING`/`RUNNING`/`SUCCESS`/`FAILED`) is coarser than what StarRocks actually
reports (`PENDING`/`SNAPSHOTING`/`UPLOADING`/`SAVE_META`/... for backup; an analogous sequence for
restore). Logging the native value preserves the additional detail StarRocks already gives us;
`SUCCESS`/`FAILED` are added as the log's own terminal vocabulary once the job resolves, since
StarRocks itself reports `FINISHED`/`CANCELLED`/errors rather than those two words.

**Dedup by comparing to the last row, not by only logging on an explicit transition.** The
`on_progress` callback already fires on every poll, including unchanged-state polls (every 10th
poll per `executor.py:204`). The append helper does the dedup check itself (read-then-maybe-insert
inside the same short-lived session) rather than pushing that logic into the callers, so
`executor.py`/`restore.py` stay simple forwarding wrappers.

**`cluster_id` is not duplicated onto history rows.** A cluster-scoped history query joins through
`Job.cluster_id`. This avoids a second source of truth for a value `Job` already owns, at the cost
of requiring a join for that one query shape — acceptable since `job_id` is already indexed via
the FK and `Job.cluster_id` is indexed today.

**Two helpers, not one parametrized by table.** `append_backup_event(job_id, status, ...)` and
`append_restore_event(job_id, status, ...)` are separate functions writing to their own table,
mirroring the two-table decision above, rather than one function taking a table/model argument.

**The backup catalog moves from `backup_history` to two new columns on `Job` itself:
`Job.label` and `Job.repository`.** Rather than inventing a separate backup-catalog table, or
keeping the old catalog columns alongside the new log columns on `backup_history`, every one of the
four call sites above is rewired to query `Job` directly: `job_type` already distinguishes
`backup_full`/`backup_incremental` (replacing `backup_type`), and `Job.status`/`Job.finished_at`
already exist. This follows the same "don't duplicate a value `Job` already owns" principle as the
`cluster_id` decision above. `Job.repository` is populated by `commands/jobs.submit_job` from the
request's `repository` param at job-creation time (mirroring how it already extracts `group_id`
today), since it's known up front. `Job.label` is not known until `labels.determine_backup_label`
computes it during `run_backup_full`/`run_backup_incremental`, so it's written onto the job's row at
that point, before `execute_backup` runs — using the same `job_id` this change already threads
through for history-event logging. Both columns are nullable (only backup jobs populate them) and
`Job.label` is indexed for the label lookups these call sites perform.

**Pruning a snapshot deletes its `Job` row instead of a `backup_history` row.**
`prune.cleanup_backup_history` today deletes the `BackupHistory` row for a pruned snapshot label;
once that table is an immutable execution log, catalog removal has to happen on `Job` instead.
Deleting the `Job` row for that `(cluster_id, label)` cascades to its `backup_history` rows via the
existing `ON DELETE CASCADE`, so the job's full execution log is removed along with it - consistent
with AGENTS.md §"Deleting a Schedule or a Cluster" already treating "removing Jobs and their
History" as an expected side effect of a deletion operation elsewhere in this codebase. This is a
deliberate choice over merely clearing `Job.label` (which would leave the job and its history behind
indefinitely): once a snapshot is pruned, nothing about that backup job remains useful to keep.

## Risks / Trade-offs

- [Dropping and recreating the tables loses existing history rows] → Acceptable: today's rows are
  single best-effort summaries with no `job_id`, already duplicative of `Job.result_json`, and of
  little value once the new log shape lands. Flagged explicitly in the proposal as **BREAKING**.
- [`on_progress` already exists but its caller wiring in `executor.py`/`restore.py` is currently
  the vehicle for the old best-effort write] → Removing the old write and adding the new one in
  the same code path risks a partial edit leaving both or neither; tasks.md sequences this as one
  atomic replacement per call site, tested by the dedup/terminal-row tests in the proposal.
- [Dedup logic requires a read before the insert, inside the same short session] → Small added
  latency per poll (one extra SELECT), acceptable given polls are already seconds apart; no
  transaction spans the StarRocks operation itself.
- [Moving restore lineage onto `Job.label`/`Job.repository` means backup jobs that completed
  before this change ships have `label = NULL`] → A restore targeting one of those pre-existing
  labels will get `BackupLabelNotFoundError` instead of resolving via the old `backup_history` row.
  Accepted for the same reason as the `backup_history`/`restore_history` drop below: this is a
  fresh-metadata boundary, not a data migration.

## Migration Plan

1. Alembic migration on top of `b7bf2d5f2365_baseline_schema`:
   - Drop `backup_history` and `restore_history`'s existing columns/unique constraint, add
     `job_id` (FK to `jobs.id`, `ON DELETE CASCADE`), `ts`, `status`, `message` (nullable),
     `details_json` (nullable).
   - Add `label` (nullable, indexed) and `repository` (nullable) columns to `jobs`.
2. Replace `history.log_backup`/`history.log_restore` with `append_backup_event`/
   `append_restore_event`.
3. Wire `on_progress` in `executor.execute_backup`/`restore.execute_restore_flow` to the new
   helpers; remove the old single-row writes at the same call sites.
4. `commands/jobs.submit_job` sets `Job.repository` from `params["repository"]` at creation time;
   `run_backup_full`/`run_backup_incremental` set `Job.label` once `labels.determine_backup_label`
   computes it, using the same short-lived-session `job_id` plumbing as the history-event wiring.
5. Rewrite `restore.find_restore_pair`/`restore.find_backup_repository` to query `Job` (by
   `cluster_id`, `label`, `job_type`, `status`, `finished_at`) instead of `backup_history`.
6. Add `GET /job/{job_id}/history`.

No rollback path preserves old `backup_history`/`restore_history` rows (see Risk above); rolling
back the migration itself is a standard Alembic downgrade dropping the new columns (including
`jobs.label`/`jobs.repository`).
