## Why

The repository needs a concise, code-grounded architecture guide so contributors can understand the StarRocks backup system, its application boundaries, and where to look before making changes. The existing root `AGENTS.md` is the right place for this guidance, but its description should clearly distinguish intended boundaries from current implementation details.

## What Changes

- Document the project purpose and its inventory groups, StarRocks repositories, scheduled backups, manual backups, and restores.
- Explain how CLI and HTTP API entry points reach application operations through the commands layer, how commands coordinate core modules, and that core modules do not call commands.
- Describe the separate SQLAlchemy/Alembic metadata access layer and the intended use of short-lived metadata transactions around asynchronous or parallel work, including any current gap found in the code.
- Explain job execution and concurrency based on the actual implementation, including the current cluster-scoped backup reservation behavior.
- Point contributors to the API specifications and implementation as the sources of API conventions.
- Keep this change documentation-only; do not modify application code or API behavior.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. This is a documentation-only change and does not alter system behavior. The change opts out of spec deltas with `skip_specs: true`.

## Impact

- Root `AGENTS.md` architecture guidance.
- No application code, public API, dependencies, or runtime behavior changes.
