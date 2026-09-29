## Context

See proposal.md. Today `commands/restore.py` resolves the source by `(cluster_id, label)`, `restore.py`
finds the chain with a time-based `find_latest_full_backup_before`, `group_id` memberships are read with the
restoring cluster's id, and `execute_restore` only *completes* a concurrency slot (no reservation).
`Job.source_backup_job_id` (FK `ON DELETE CASCADE`, satisfying Q6) already exists. Repositories are not
persisted (PLAN 0.1); `SHOW REPOSITORIES` returns location but never credentials.

## Goals / Non-Goals

**Goals:**
- Restore addressed by `source_job_id`, optionally into another cluster, full backups only cross-cluster.
- Fail fast, with an actionable error, when the target lacks the repository.

**Non-Goals:**
- Creating repositories or storing/copying credentials (the existing repository endpoint does that).
- Cross-cluster incremental restore.
- Fixing the multi-database incremental baseline gap (see Risks).
- Moving `restore_history` into a shared `job_events` table.

## Decisions

1. **Job ownership.** `Job.cluster_id` stays the source cluster (matches the path and the history filter);
   add nullable `Job.target_cluster_id` (null = same cluster). Everything that acts on where the restore
   *executes* uses `COALESCE(target_cluster_id, cluster_id)`. Alternative rejected: `cluster_id` = target
   with no new column - less code but departs from PLAN 11.1's schema and the path semantics.
   Sites to change (enumerate and test each): dispatch/admission lane and "no RUNNING job on cluster"
   check (`dal/metadata/jobs.py`, `commands/jobs.py`); job handler connecting to the cluster
   (`run_restore` receives the target); concurrency slot reservation/completion; stale-job reconcile
   (`_operation_targets`, `_fail_job`); active-restore protection in retention
   (`dal/metadata/retention.py`, keyed by source job, unaffected); `cluster_cleanup` (PLAN 12.1).
2. **Keep the column name `source_backup_job_id`** (already migrated); PLAN's `source_job_id` is the API
   field name only. New Alembic migration adds only `target_cluster_id` (FK `clusters.id`).
3. **Keep `restore_history`.** The archived add-job-history-log design deliberately chose per-kind history
   tables; PLAN 11.1's "drop restore_history in favour of job_events" is not implemented.
4. **Repository check at submit, live.** Read the source repository's location from the source cluster's
   `SHOW REPOSITORIES` (references only store the name), find a target repository with the same location
   (normalise trailing `/`), and fail 409 naming the source repository and location if none; 502/503 if a
   cluster is unreachable. Rationale: schedule creation already validates repositories live (PLAN 3.1) and
   the operator gets the fix instruction immediately. Alternative rejected: failing at execution.
   The chosen target repository name is re-resolved at execution (repos can change between submit and run).
5. **Chain from the source Job**: full -> `[source]`; incremental -> `[baseline_job, source]` via
   `baseline_job_id`, same-cluster only. Snapshot labels/timestamps come from the jobs' references and the
   target's `SHOW SNAPSHOT`.
6. **Filters from references on the source cluster.** `group_id` must belong to the source cluster (422);
   memberships are matched against the job's `BackupReference` rows, a `*` membership matching every
   referenced table of that database. This removes the live `SHOW TABLES` call.
7. **Create the database on the target** (`CREATE DATABASE IF NOT EXISTS`) before `RESTORE`. The existing
   `_restored` + atomic rename flow already tolerates a missing live table.
8. **Concurrency.** Reserve a slot on the target cluster at execution start (today only completion exists)
   and release it in `finally`. The slot scope must be chosen so a restore and a backup on the target cannot
   run together (SPEC §19: one job per cluster of any kind) - the dispatcher already guarantees this per
   lane; the reservation is defence in depth. No metadata transaction stays open while StarRocks runs
   (AGENTS.md).
9. **Route retirement.** Remove `/backup/manual/restore/...`; add the new routes under `commands/`
   (API calls `commands.restore` only). Breaking API change documented in the spec delta.

## Risks / Trade-offs

- [Multi-database incremental stores one `baseline_job_id` (first database only, `commands/backup.py`)] ->
  same-cluster incremental restore inherits this gap; out of scope, recorded as a known gap. Cross-cluster is
  full-only, so it is unaffected.
- [`RESTORE` may require the database to exist / behave differently on an empty cluster] -> verify with the
  integration test; the create-database step is unconditional either way.
- [Repository location match is string-based; the same bucket via different endpoints/aliases won't match]
  -> 409 tells the operator exactly what to create; normalise only trailing slashes.
- [Repository changes between submit and execution] -> re-resolve at execution and fail the Job with a clear
  history event.
- [Deleting the source schedule/cluster or retention dropping data while the restore runs on another
  cluster] -> retention already protects open restore sources; schedule delete already rejects on an open
  restore (SPEC §24); `cluster_cleanup` (PLAN 12) must do the same for restores that read the deleted cluster's data.
- [Integration test needs two StarRocks clusters sharing one S3] -> check whether the local stack provides a
  second cluster; otherwise the cross-cluster integration test is added when the stack does (PLAN 13.2).

## Migration Plan

Alembic migration adding nullable `jobs.target_cluster_id`; no backfill (null = same cluster). Existing
restore jobs keep working. Rollback: drop the column. The route/body change is breaking for clients of the
old restore route.
