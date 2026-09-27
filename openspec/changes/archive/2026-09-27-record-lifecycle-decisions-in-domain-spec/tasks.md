## 1. `SPEC.md` — Retention Job as a domain entity

- [x] 1.1 Add `Retention Job` to the §2 domain model text/relationships (`Schedule -> Retention Job (1:N)`, `Retention Job -> Backup Job` references) as a peer of Backup Job/Restore Job. Verify by re-reading §2: it lists three job-shaped entities, each with the same "own id, status, history" framing.
- [x] 1.2 Add a short subsection after §22 (Failed Jobs and Retention) describing Retention Job's lifecycle: created after a recurring full-backup job succeeds, has a status, has its own append-only history, references the Backup Jobs it drops. Verify by confirming the new subsection reads consistently with how §17/§18 introduce Backup History/Status right after Backup Job.

## 2. `SPEC.md` — one-shot expiry

- [x] 2.1 Amend §8 (Manual Schedule) to state a one-shot schedule may define an expiry (never, or after N days), after which it is deleted the same way an operator-deleted schedule is (cross-reference task 4.1's new section). Verify by re-reading §8: it no longer implies a one-shot schedule can only be removed by an explicit operator delete.

## 3. `SPEC.md` — retention's incremental/baseline exemption

- [x] 3.1 Amend §10-11 (Retention) to state retention considers only successfully completed full backups. Verify: §10's definition of retention now says "full backups," not "backups."
- [x] 3.2 Amend §21 (or add adjoining text) to state a full backup that is the baseline of an existing incremental backup is never dropped by retention, regardless of the retention count, and that this exemption lapses once no incremental depends on it. Verify: the two rules (incrementals are outside retention; baseline-full is conditionally exempt) are stated as distinct sentences, per design.md's Decisions.
- [x] 3.3 Amend §22 (Failed Jobs and Retention) to add a worked example analogous to the existing one, showing an incremental depending on a full backup that would otherwise fall outside the retention count. Verify: the new example names which job is protected and why, mirroring the existing Job 1/Job 3/Job 5 style.

## 4. `SPEC.md` — cascading delete as an async operation

- [x] 4.1 Add a new section (placed after §22, before §23 Restore Job) describing schedule deletion (operator-initiated or automatic expiry) as: accepted synchronously (rejected if a job is still `PENDING`/`RUNNING`, or if it would remove a full backup another schedule's incremental depends on), then executed as a background operation that deletes the schedule's Backup Jobs, their History and References, and the corresponding Repository snapshots. Verify: the section makes clear this is not a single-request operation.
- [x] 4.2 In the same section, describe cluster deletion identically: allowed only when the cluster has no Schedules (enabled or disabled), then cascading the same way. Verify: the wording explicitly says "enabled or disabled," not just "no schedules."
- [x] 4.3 Amend §25 (Restore Job) to note a Restore Job is deleted along with the Backup Job it restored from (and, for cluster deletion, along with either the source or target cluster). Verify: re-reading §25 alongside §13/§16 does not contradict "a Job is a historical fact" — the new sentence scopes the exception to Restore Job only.

## 5. `AGENTS.md` — concurrency and async-delete note

- [x] 5.1 Rewrite the "Parallel execution and metadata" paragraph's framing of per-cluster backup serialization: state it as the confirmed policy, not a gap to reconsider. Verify: the paragraph no longer says "current behavior... is a gap to account for" about backup serialization itself (the session/transaction-lifetime gap it also describes stays, since that one is still open — see `PLAN.md` section 7).
- [x] 5.2 Add to the same paragraph that a Retention Job reserves the same `backup` concurrency scope as backup jobs, so retention and a backup on the same cluster can never run concurrently. Verify: the scope name `backup` is stated explicitly, matching `concurrency.reserve_job_slot`'s existing scope string.
- [x] 5.3 Add a note that schedule deletion and cluster deletion follow the same pattern as backup submission: the API call does synchronous validation and returns immediately, and the actual StarRocks/S3 cleanup runs as a job — consistent with the existing rule against holding a transaction open across a StarRocks operation. Verify: the note cross-references that existing rule rather than restating it as a new one.

## 6. Cross-check

- [x] 6.1 Re-read all edited sections of `SPEC.md` end to end and confirm no other section (§13, §16, §17-19) contradicts the new Retention Job entity or the cascading-delete section. Verify: no remaining sentence implies a Job (of any type) is deleted except where §4.1-4.3 now say so.
- [x] 6.2 Confirm `PLAN.md` (already updated earlier in this change's conversation) uses the same job-type names (`schedule_cleanup`, `cluster_cleanup`, `retention`) and the same synchronous-validate-then-async-job phrasing as this change's `SPEC.md`/`AGENTS.md` edits. Verify: `grep -n "schedule_cleanup\|cluster_cleanup" PLAN.md SPEC.md AGENTS.md` shows consistent naming across all three files.
- [x] 6.3 Run `openspec validate --changes record-lifecycle-decisions-in-domain-spec` and confirm it passes with the existing `skip_specs: true` zero-delta acceptance.
