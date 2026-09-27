## Context

See `proposal.md` - Why. This change is documentation-only, but several of the decisions it
records are technical choices that later, code-bearing changes (`PLAN.md` sections 5, 6, 8, 9,
10, 11, 12) will implement without revisiting them. This document fixes the exact wording,
naming, and placement choices so those later changes have a single source to implement against,
rather than each re-deriving them from the conversation that produced them.

Current `SPEC.md` and `AGENTS.md` state relevant here:
- `SPEC.md` §2 lists only `Backup Job` and `Restore Job` as job-shaped entities; retention is
  described only as a rule (§10-11, §21-22), with no entity of its own.
- `SPEC.md` §8 describes a one-shot schedule as immutable and deletable, with no expiry.
- `SPEC.md` §16/§22 already establish "a failed job is not deleted" and "failed jobs are ignored
  by retention," but say nothing about incremental backups (not mentioned anywhere in `SPEC.md`)
  or about a full backup that an incremental depends on.
- `AGENTS.md`'s "Parallel execution and metadata" paragraph frames per-cluster backup
  serialization (`concurrency.reserve_job_slot` scope `"backup"`) as a *current limit* to
  reconsider, not a settled policy.
- `api-cluster-registry`'s delete guard and its implementation (`commands/clusters.py`) block
  cluster delete only on *enabled* schedules, and delete the cluster row synchronously in the
  same request.
- `api-scheduling`'s delete scenario removes a schedule synchronously ("the system removes it
  from the registry") with no mention of its backup data.

## Goals / Non-Goals

**Goals:**
- Fix the exact `SPEC.md` wording and diagram changes for: Retention Job as a domain entity,
  one-shot expiry, the incremental/baseline retention exemption, and schedule/cluster deletion
  as synchronously-validated + asynchronously-executed cascades.
- Fix the exact `AGENTS.md` wording for the concurrency-scope and async-cascade updates.
- Name the two new job types (`schedule_cleanup`, `cluster_cleanup`) and the concurrency scope
  decision for `retention`, so later changes implement against agreed names, not ad hoc ones.
- Make explicit which later `PLAN.md` sections carry the actual schema/API/code work, so this
  change cannot be mistaken for having implemented any of it.

**Non-Goals:**
- No schema, migration, model, endpoint, or job-backend code changes. Nothing here is testable
  by running the application; it is reviewed by reading the edited `SPEC.md`/`AGENTS.md` text.
- No `openspec/specs/api-*` capability spec changes (`skip_specs: true` — see proposal.md
  Capabilities). The endpoint-level consequences (e.g., `DELETE` returning `202`) are specified
  when sections 8 and 12 are implemented, each against the wording fixed here.
- Not resolving how `cluster_cleanup` and `schedule_cleanup` share code (e.g., whether cluster
  cleanup calls schedule cleanup once per schedule, or reimplements the same drop-and-delete
  logic against a wider row set) — that is an implementation choice for section 12, not a
  domain-spec concern.

## Decisions

**Retention Job is added to `SPEC.md` §2 as a domain entity, not folded into Schedule.**
Once retention has its own id, status, and append-only history (decided in conversation: it runs
as its own job type with its own event log), it meets the same bar `SPEC.md` already uses for
Backup Job and Restore Job ("a specific execution... tracked... own history"). Treating it as a
rule of Schedule instead would leave the domain model unable to express "which retention run
dropped this snapshot, and when" — the same gap §17 identifies for backups before Backup History
existed. Placement: a new relationship `Schedule -> Retention Job (1:N)` and `Retention Job ->
Backup Job (references the jobs it drops)` alongside the existing Backup Job / Restore Job
relationships in §2; a short new subsection after §22 (Failed Jobs and Retention) spelling out
its lifecycle, mirroring how §17/§18 describe Backup History/Status right after Backup Job is
introduced.

**One-shot expiry is a property of Schedule (`expire_after_days`), not a separate mechanism.**
Alternative considered: a background sweep with its own retention-like rule, independent of the
schedule's own fields. Rejected because a one-shot schedule already has exactly one job; giving
it an expiry field keeps "when does this schedule stop mattering" answerable by reading the
schedule alone, and reuses the same delete path (§8's cascade) rather than inventing a second
deletion mechanism with its own edge cases.

**Retention's incremental exemption is stated as two rules, not one.** (1) Incremental backups
are never subject to retention at all. (2) A full backup is exempt from retention specifically
while it is the baseline of an existing incremental, regardless of the schedule's `retention`
count. These are kept distinct in the `SPEC.md` wording because they have different scopes: (1)
is permanent (incrementals are simply outside retention's domain), while (2) is conditional and
can lapse once the dependent incremental is itself gone. Conflating them into one sentence would
obscure that a full backup can re-enter retention's count later.

**Schedule/cluster delete: synchronous validation + asynchronous cascade, not a single
transaction.** Alternative considered: delete everything inside the `DELETE` request using
FK `ON DELETE CASCADE`, relying on the database to remove jobs/events/references in one
transaction. Rejected: `AGENTS.md`'s existing architectural rule ("a database transaction should
not remain open while StarRocks runs") already forbids holding a transaction open across the
`DROP SNAPSHOT` calls this requires — cascade deletion here is not just row deletion, it is a
sequence of live StarRocks/S3 operations per reference. The chosen shape mirrors backup
submission exactly: the API call does synchronous checks that can be answered from metadata alone
(is a job active? does a dependent incremental exist?), then hands off to a job for everything
that touches StarRocks/S3. This is a **breaking change** to both `DELETE` endpoints' current
contracts (§8.7/§12.2 in `PLAN.md`), which is why it is called out explicitly rather than treated
as an implementation detail.

**New job types are named `schedule_cleanup` and `cluster_cleanup`, not a single generic
`cleanup` type.** Alternative considered: one `cleanup` job type parameterized by target
(`schedule_id` xor `cluster_id`). Rejected for consistency with how every other job type in this
system names the operation, not a generic verb (`backup_full`, `backup_incremental`, `restore`,
`prune`, `retention`) — `list_jobs`/`JobRead` filtering by `job_type` stays meaningful, and a
`GET /job/{id}/history` reader does not need to inspect params to know what happened. Cluster
cleanup is not defined as "run schedule cleanup once per remaining schedule" because, by the time
it runs, decision 0.8 guarantees zero schedules exist — what is left to clean up (orphaned jobs
predating schedule linkage, repositories, cross-cluster restore jobs) is a different row set than
schedule cleanup ever touches.

**Retention shares the `backup` concurrency scope; it does not get its own scope.** Alternative
considered: a separate `retention` scope, allowing a retention run and a backup to proceed
concurrently on the same cluster. Rejected: retention drops snapshots via StarRocks/S3 while a
concurrent backup could be reading the repository's catalog or writing a new snapshot; reusing
the existing `backup` scope in `concurrency.reserve_job_slot` costs nothing new to build and
removes an entire class of race to reason about, at the cost of a retention run occasionally
queuing behind a backup (acceptable — retention already only runs after a backup succeeds on the
same cluster, so contention is rare, not structural).

**Restore Job is deleted with its source Backup Job**, and cluster delete removes restore jobs
where the cluster is either source or target — both decided in conversation (Q6, Q7) on the
grounds that a Restore Job's only value is as a record of what was restored from where; once its
source is gone (or the cluster it ran against no longer exists), keeping an orphaned record adds
bookkeeping without preserving anything restorable. This is recorded here as a design rationale
because it reverses the more common pattern in this system (a Job is a permanent historical fact,
per §13/§16) — the exception is explicit and scoped: Backup Jobs remain permanent facts; only a
Restore Job's own history is disposable, and only when it loses its reason for existing.

## Risks / Trade-offs

- [Documenting an async delete contract before any code exists] → later changes (8, 12) might
  discover the synchronous pre-checks are more expensive than assumed (e.g., "does this full
  backup baseline an incremental" requires a join across schedules) and need a different check
  shape. Mitigated by keeping the domain-spec wording about *what* is checked, not *how*
  (no query plan implied), so the check can be implemented efficiently without contradicting
  `SPEC.md`.
- [Two new job types before the job-event-log change (`PLAN.md` section 4) lands] → until then,
  `schedule_cleanup`/`cluster_cleanup` are named in `SPEC.md`/`PLAN.md` but have no event
  vocabulary defined. Mitigated by leaving their event names to section 4/8/12 rather than
  inventing them here — this change only commits to the job types existing and what they do, not
  their event log contents.
- [Naming drift between this change's job-type names and what section 8/12 actually implement] →
  since no code exists yet, `schedule_cleanup`/`cluster_cleanup` could still change during
  implementation. Mitigated by treating this design doc, not memory, as the source of truth: if
  section 8/12 implementation wants a different name, it updates this change's design.md (or its
  own) rather than silently diverging.

## Migration Plan

Documentation-only; no deployment or rollback mechanics apply. Sequencing that matters:
1. Land this change (`SPEC.md`, `AGENTS.md`, `PLAN.md` edits) before starting section 5
   (schedule fields) or later, since those sections' tasks already assume this wording.
2. Archive this change once the edits are made and reviewed — nothing here waits on tests, since
   there is no code to test.

## Open Questions

None — every ambiguity surfaced while drafting this change (retention as an entity, sync/async
delete, job-type naming, concurrency scope) was a decision that would have changed the spec
wording or the task breakdown, so each was resolved above rather than deferred.
