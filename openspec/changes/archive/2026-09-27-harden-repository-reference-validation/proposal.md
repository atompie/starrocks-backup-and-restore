## Why

Repository is deliberately not a persisted entity (PLAN.md 0.1, reversed 2026-09-27): StarRocks itself is the source of truth, and every reference to one is `(cluster, name)`, validated live. That live-validation pattern already exists and is already correctly scoped per cluster, but the invariant that makes it safe — a fresh connection to the exact target cluster, no shared/cached repository state — is implicit in the code rather than stated anywhere, and no test would catch a regression that let a same-named repository on a different cluster slip through. This change confirms the existing behavior, closes that test gap, and writes the `(cluster, name)` reference pattern down in `SPEC.md` §4 so it's explicit for this section and for the entities that will reference repositories the same way later (Backup Reference, restore target — sections 6 and 11).

## What Changes

- Confirm `Schedule.repository` stays a plain string column, with both create and update validating it live against `SHOW REPOSITORIES` on the schedule's own cluster via the existing `ensure_repository_exists` helper (`api/routes/_cluster_connect.py`). No code change is needed here — this is a verification, not a fix.
- Add a same-cluster cross-check test: a repository name that exists only on a different cluster is rejected (404) when referenced by a schedule create or update on the wrong cluster.
- Document the `(cluster_id, name)` repository reference pattern in `SPEC.md` §4, noting it applies wherever a repository is referenced today (Schedule) and wherever it will be referenced later (Backup Reference, restore target — not yet implemented, forward-looking).
- No change to `openspec/specs/api-*` requirement text: `api-scheduling` already documents the "repository must exist on that cluster" behavior for create and update: this change verifies and tests that existing contract rather than altering it.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None — no spec-level (`openspec/specs/api-*`) requirement changes. `api-scheduling`'s existing requirements already describe this behavior; see `.openspec.yaml`'s `skip_specs: true`.

## Impact

- Tests: `tests/unit/service/test_api_schedules.py` gains a cross-cluster repository rejection test for create and update.
- Docs: `SPEC.md` §4 (Repository) gains a short subsection on the `(cluster_id, name)` reference pattern.
- No changes to `src/starrocks_br/` runtime code, migrations, or API contracts.
