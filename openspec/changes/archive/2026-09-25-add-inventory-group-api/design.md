## Context

`table_inventory` lives in each target cluster's `ops` schema (`schema.py:125-138`,
`get_table_inventory_schema`) and is populated today only via `schema.bootstrap_table_inventory`
(raw f-string SQL) or manual SQL. The existing API already has an analogous CRUD surface,
`repositories.py`, that talks to StarRocks directly (no local ORM cache) and follows a
`get_cluster_or_404` → `connect_or_503` → domain call → `close()` shape, duplicated verbatim across
`repositories.py`, `jobs.py`, and `clusters.py`. See proposal.md for motivation.

## Goals / Non-Goals

**Goals:**
- CRUD API for inventory groups that reads cluster state live, with no local caching, matching the
  `repositories.py` precedent.
- Close the async-`KeyError` failure mode for missing/unknown `group` on backup submission with a
  synchronous check.
- Remove the SQL-injection risk in `bootstrap_table_inventory` without changing its idempotent
  "re-running init only adds new rows" behavior.

**Non-Goals:**
- No "back up everything" implicit default when `group` is omitted — `group` stays a required,
  explicit parameter. Introducing an implicit "all tables" fallback would silently widen what a
  backup call does; that's a separate, deliberate decision this change does not make.
- No CLI commands for inventory groups (API only, this change).
- No change to `get_table_inventory_schema`'s DDL.
- No per-endpoint job-payload schema split (`JobSubmitRequest` decomposition) — tracked as a
  separate follow-on change that depends on this one's `_submit_backup_job` helper.

## Decisions

**Domain module mirrors `repository.py`, not an ORM model.** `inventory_groups.py` does
SQL-building + `StarRocksDB` calls with no HTTP knowledge, consistent with how repository data
(which also lives on the cluster, not in this tool's local DB) is handled. Alternative considered:
model `table_inventory` as a SQLAlchemy-backed local entity — rejected because the source of truth
is the cluster itself, and a local cache would drift from concurrent manual SQL or CLI bootstrap
changes.

**Group existence is checked with a fast synchronous SQL lookup before job creation, not deferred
to the async job.** This directly targets the reported bug: `KeyError` surfacing only after 202 has
already been returned. Alternative considered: keep the check only in the async job handler and
just improve the error message — rejected because the caller still gets a false-positive 202 and
has to poll to discover the failure.

**Routes are a thin HTTP/error-translation layer; SQL logic stays in the domain module.** Exception
types (`InventoryGroupNotFoundError`, `InventoryMembershipConflictError`,
`InventoryMembershipNotFoundError`) are translated to 404/409 in the router, matching how
`repositories.py` and `jobs.py` already translate domain errors to HTTP status codes.

**Deduplicate `_connect`/`_connect_or_503`/`_get_cluster_or_404` into
`api/routes/_cluster_connect.py` as part of this change**, rather than adding a third copy for the
new router. This is a small refactor bundled with this change because it directly blocks writing
the new router cleanly; `repositories.py`, `jobs.py`, and `clusters.py` are updated to import from
the shared module with no behavior change (verified by running their existing test suites before
proceeding to new work).

**All interpolated SQL values use `utils.quote_value`/`utils.quote_identifier`.**
`bootstrap_table_inventory`'s existing raw-interpolation pattern is the thing being fixed, not a
precedent to repeat. `bootstrap_table_inventory` is changed to delegate row insertion to
`inventory_groups.add_membership`, catching `InventoryMembershipConflictError` as a no-op to
preserve today's idempotent bootstrap behavior.

**`table_name` accepts any non-empty string including `'*'`** (the documented "all tables in
database" wildcard) — validated only via `Field(min_length=1)` at the schema layer, no additional
allow-listing, since StarRocks itself is the source of truth for whether a name is valid.

## Risks / Trade-offs

- **[Risk]** The synchronous group-existence check adds one extra round-trip to StarRocks on every
  backup submission → **Mitigation**: it's a single indexed lookup (`LIMIT 1` on a unique-key
  prefix), and it replaces a failure mode that previously cost a full async job cycle plus a client
  poll to discover.
- **[Risk]** `add_memberships_bulk`'s partial-success-on-loop semantics (matching existing bootstrap
  behavior) mean a group creation request with N tables can partially succeed if one insert fails
  after others commit → **Mitigation**: this matches pre-existing bootstrap idempotency
  expectations; a client can safely retry the same creation request since duplicates are silently
  deduped by the UNIQUE KEY.
- **[Risk]** Marked **BREAKING** in the proposal: any caller relying on today's 202-then-async-fail
  behavior for missing groups changes to a synchronous 404 → **Mitigation**: no caller could have
  gotten a successful backup from a missing/unknown group before, so no working integration's
  outcome changes, only the timing/shape of the error.

## Migration Plan

No data migration — `table_inventory`'s DDL is unchanged. Rollout is a normal code deploy:
1. Land the `_cluster_connect.py` extraction and confirm existing route tests are unchanged.
2. Land `inventory_groups.py`, its router, and schemas; register the router in `app.py`.
3. Land the `bootstrap_table_inventory` fix and the `jobs.py`/`handlers.py` group-existence check
   together, since they share the same underlying `group_exists` check.
No feature flag — this is additive (new endpoints) plus a bug fix (fail-fast on missing group), and
the repo's convention is no backwards-compatibility shims for internal APIs.
