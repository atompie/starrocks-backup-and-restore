## 1. Align Scheduled Backup Inputs

- [ ] 1.1 Add repository to persisted schedule and API schemas, including an Alembic migration and a safe rollout policy for existing schedules; verify migration upgrade and downgrade behavior.
- [ ] 1.2 Preserve and validate repository through schedule create, update, and read flows; verify schedule API behavior matches the existing scheduling contract.

## 2. Unify Backup Command Dispatch

- [ ] 2.1 Ensure manual full and incremental submissions and due-schedule jobs carry equivalent group, repository, and type-specific parameters into the existing per-type backup commands; verify command handler dispatch for both job types.
- [ ] 2.2 Keep route-specific validation and asynchronous submission intact while removing any duplicate backup execution logic; verify existing manual job response and error behavior remains unchanged.

## 3. Verify Shared Execution Paths

- [ ] 3.1 Add or update command and schedule tests proving manual and scheduled full/incremental jobs invoke the same command with equivalent context; verify both backup types and both invocation sources are covered.
- [ ] 3.2 Run the relevant API, schedule, job, and backup command test suites and confirm they pass.
