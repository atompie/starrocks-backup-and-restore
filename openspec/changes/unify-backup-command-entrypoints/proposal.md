## Why

Manual and scheduled backups enter through different API areas, but both represent the same use case: back up an inventory group on a cluster. Keeping their application dispatch separate risks different validation, parameters, or execution behavior as backup capabilities evolve.

## What Changes

- Route manual full and incremental backup submissions and due-schedule dispatch through shared commands, with one command for each backup type.
- Keep the manual and schedule HTTP routes and their existing request/response behavior separate at the API boundary.
- Ensure the shared command receives the cluster and inventory group context, along with the type-specific backup options, regardless of invocation source.
- Preserve asynchronous job execution while consolidating backup use-case dispatch.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. This is an internal command architecture refactor with no intended change to externally observable API behavior.

## Impact

- Affects `commands/backup.py`, `commands/schedules.py`, `commands/jobs.py`, job handlers, and the manual and schedule API route adapters.
- May affect schedule persistence and job payload construction to ensure scheduled backup options reach the shared commands.
- Does not change API paths, response shapes, or the asynchronous job model.
