## Context

See proposal.md - Why. `ensure_repository_exists(cluster, repository_name)` (`api/routes/_cluster_connect.py:55-73`) already opens a fresh live connection scoped to the exact `Cluster` row passed in and lists repositories via `SHOW REPOSITORIES` on that connection alone; there is no shared cache or cross-cluster state to leak through. It is called from `api/routes/schedules.py` on schedule create (`:74`) and update (`:127-128`), each time with the `Cluster` fetched for that request's own `cluster_id`. This is a verification-and-test change, not a redesign: the routes-layer placement of this check (rather than behind `commands/schedules.py`) is a known, accepted exception, matching the inventory-group pattern that section 12.3 of PLAN.md is scoped to fix later.

## Goals / Non-Goals

**Goals:**
- Verify, and add regression coverage for, the invariant that a schedule can only reference a repository that exists on its own cluster.
- Record the `(cluster_id, name)` repository reference pattern in `SPEC.md` §4 as the general pattern this system uses, including for entities that don't exist yet.

**Non-Goals:**
- Moving repository validation from `api/routes/schedules.py` into `commands/schedules.py` (deferred to PLAN.md 12.3, alongside inventory-group routes).
- Any change to `ensure_repository_exists`'s signature or behavior; it is already correctly scoped.
- Implementing Backup Reference or restore-target repository lookups (sections 6 and 11) - §4's update only documents that they will follow the same pattern.

## Decisions

- **No code change to the validation helper.** `ensure_repository_exists` already takes a full `Cluster` object and opens a per-cluster connection, so cross-cluster leakage is structurally prevented by construction, not by an explicit id check. Alternative considered: refactor it to accept `cluster_id` and re-fetch/re-verify the cluster internally, as defense against a future caller passing the wrong `Cluster` object. Rejected for this change: no such caller exists today (both call sites derive `cluster` from the same request's `cluster_id`), and adding an internal re-fetch would be speculative hardening against a bug that isn't there - it can be revisited if section 12.3's commands-layer move surfaces a real call site that needs it.
- **Test placement**: add the cross-cluster case to `tests/unit/service/test_api_schedules.py`, next to the existing `test_create_schedule_unknown_repository_404` / `test_update_schedule_unknown_repository_404`, using two mocked clusters with disjoint `list_repositories` results (mirroring the existing `_mock_repository_check` / monkeypatch pattern in that file) rather than a live integration test - this is unit/service-level (validation/dispatch logic), not a CRUD or infrastructure concern.
- **SPEC.md §4 update is additive-only**: a new subsection under the existing "Reference" heading, not a rewrite, since the existing text already correctly describes live per-cluster validation for the entities that exist today.

## Risks / Trade-offs

- [The `Cluster`-object-trust pattern in `ensure_repository_exists` could regress if a future refactor passes the wrong cluster object] → Mitigated by the new cross-cluster test, which would fail if a caller ever mixed up cluster context, and by leaving an explicit non-goal note here for section 12.3 to reconsider when it moves this logic.
- [Documenting Backup Reference / restore-target reference behavior in SPEC.md before they're implemented could drift from what sections 6 and 11 actually build] → Kept intentionally brief and phrased as "will use the same pattern," matching how SPEC.md already describes other not-yet-built entities elsewhere; sections 6 and 11 own the authoritative detail when implemented.
