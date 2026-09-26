## Context

The API currently uses two adapters for backup initiation. Manual backup routes in `api/routes/jobs.py` validate group and repository, then submit a job through `commands.jobs.submit_job`. Due schedules in `commands/schedules.py` also submit jobs, but construct params from only the stored inventory group id. The worker handler maps `backup_full` and `backup_incremental` to `commands.backup.run_backup_full` and `run_backup_incremental`; those commands already contain the shared StarRocks backup execution logic and require a repository.

The schedule specification describes schedule job type, group, repository and backend as inputs, while the current schedule model and route do not persist repository. Preserve the documented schedule behavior while aligning actual schedule data and dispatch with the common command flow.

## Goals / Non-Goals

**Goals:**

- Keep one shared command per backup type and ensure both invocation sources execute through those commands.
- Keep HTTP concerns in their route adapters and preserve asynchronous job submission.
- Pass the complete backup context needed by the command from manual requests and persisted schedules.

**Non-Goals:**

- Change API route paths or response semantics.
- Consolidate restore or prune commands as part of this backup-specific change.
- Change the StarRocks backup algorithm or metadata transaction policy.

## Decisions

- **Treat `commands.backup.run_backup_full` and `run_backup_incremental` as the canonical backup use cases.** Keep the job handler as a type-to-command adapter; it must not contain backup behavior. The manual API adapter and the scheduled job path both ultimately dispatch each backup type to the corresponding command. Alternative: add another command layer that forwards to the existing functions; this would add indirection without sharing additional behavior.

- **Keep source-specific validation at the entry point and execution validation in the commands.** The manual route retains synchronous API checks and HTTP error translation. Schedule CRUD validates its own persisted context. The shared commands continue to enforce domain-level requirements when executing, regardless of how a job was created. Alternative: move HTTP pre-validation into the backup command, which would couple application use cases to request-time API semantics and cannot guarantee scheduled jobs are valid at execution time.

- **Make the scheduled job payload complete using schedule data.** Align schedule persistence and due-job payload construction with the schedule contract, including repository and backend, so a due job calls the same backup command with equivalent context. The backup command inputs remain cluster, group id and type-specific options. Alternative: look up a repository implicitly during execution; that makes results depend on mutable server-side defaults and diverges from the documented schedule contract.

- **Preserve the distinct route adapters.** Manual and schedule endpoints remain separate; only their application dispatch converges on the shared job and backup command path. No API contract change is intended.

## Risks / Trade-offs

- [Existing schedule storage omits repository despite the scheduling spec requiring it] → Include the necessary schedule data-model and migration work, and ensure existing schedule rows are handled safely during migration.
- [Validation at job creation can become stale before scheduled execution] → Keep command-level validation authoritative at execution time; schedule CRUD validation remains a fast feedback check.
- [Duplicate backup execution paths could remain accidentally] → Update handler/dispatch wiring and cover both manual and due-schedule paths with command-level tests.

## Migration Plan

1. Extend or reconcile schedule persistence and API schemas with the repository field required by the established scheduling contract, including a migration strategy for existing rows.
2. Route due-schedule job creation through the common job submission path with the complete stored backup context.
3. Keep manual route submission and worker dispatch wired to the same full/incremental commands.
4. Rollback by reverting the application and migration changes together; retain compatibility for already persisted schedules during rollout.
