## Context

See proposal.md - Why. The current routing has four routers (`jobs`, `schedules`, `repositories`, `inventory_groups`) that all prefix their cluster-scoped endpoints with `/cluster/{cluster_id}/...`, plus one router (`clusters`) that legitimately owns the `/cluster` and `/clusters` prefixes because cluster management *is* its domain. None of the four routers currently declare a shared prefix in code — each path is written out per-route — so this is a per-route string change, not a prefix swap.

The target convention, fixed for this change:

```
/{domain-or-service}/{operation}/{context}/{identifiers}
```

## Goals / Non-Goals

**Goals:**
- Every endpoint in the four affected routers starts with its real domain (`backup`, `repositories`/`repository`... see below, `inventories`/`inventory`), never `/cluster`.
- One consistent labeling rule for trailing identifiers, applied uniformly.
- Zero old paths left reachable — no aliases, no redirects.

**Non-Goals:**
- Changing request/response schemas, validation, auth, or business logic (proposal.md - Impact).
- Touching `/cluster`, `/clusters`, `/health`, or `GET /job/{id}` (already domain-first or context-free; see decisions below).
- Introducing API versioning (e.g. `/v1/...`) — out of scope, not requested.

## Decisions

**1. Full path table** (authoritative for tasks.md):

| Router | Method | Old path | New path |
|---|---|---|---|
| jobs.py | POST | `/cluster/{cluster_id}/backups/full` | `/backup/manual/full/cluster/{cluster_id}` |
| jobs.py | POST | `/cluster/{cluster_id}/backups/incremental` | `/backup/manual/incremental/cluster/{cluster_id}` |
| jobs.py | POST | `/cluster/{cluster_id}/restores` | `/backup/manual/restore/cluster/{cluster_id}` |
| jobs.py | POST | `/cluster/{cluster_id}/prunes` | `/backup/manual/prune/cluster/{cluster_id}` |
| jobs.py | GET | `/job/{job_id}` | unchanged |
| schedules.py | POST | `/cluster/{cluster_id}/schedules` | `/backup/schedules/cluster/{cluster_id}` |
| schedules.py | GET | `/cluster/{cluster_id}/schedules` | `/backup/schedules/cluster/{cluster_id}` |
| schedules.py | GET | `/cluster/{cluster_id}/schedule/{schedule_id}` | `/backup/schedules/cluster/{cluster_id}/schedule_id/{schedule_id}` |
| schedules.py | PATCH | `/cluster/{cluster_id}/schedule/{schedule_id}` | `/backup/schedules/cluster/{cluster_id}/schedule_id/{schedule_id}` |
| schedules.py | DELETE | `/cluster/{cluster_id}/schedule/{schedule_id}` | `/backup/schedules/cluster/{cluster_id}/schedule_id/{schedule_id}` |
| schedules.py | POST | `/schedules/run-due` | `/backup/schedules/run` |
| repositories.py | POST | `/repositories/verify` | unchanged |
| repositories.py | GET | `/cluster/{cluster_id}/repositories` | `/repositories/cluster/{cluster_id}` |
| repositories.py | POST | `/cluster/{cluster_id}/repositories` | `/repositories/cluster/{cluster_id}` |
| repositories.py | DELETE | `/cluster/{cluster_id}/repositories/{name}` | `/repositories/cluster/{cluster_id}/name/{name}` |
| inventory_groups.py | GET | `/cluster/{cluster_id}/inventory-groups` | `/inventories/cluster/{cluster_id}` |
| inventory_groups.py | POST | `/cluster/{cluster_id}/inventory-groups` | `/inventories/cluster/{cluster_id}` |
| inventory_groups.py | GET | `/cluster/{cluster_id}/inventory-groups/{group_id}` | `/inventory/cluster/{cluster_id}/group_id/{group_id}` |
| inventory_groups.py | POST | `/cluster/{cluster_id}/inventory-groups/{group_id}/tables` | `/inventory/cluster/{cluster_id}/group_id/{group_id}/tables` |
| inventory_groups.py | DELETE | `/cluster/{cluster_id}/inventory-groups/{group_id}/tables/{database_name}/{table_name}` | `/inventory/cluster/{cluster_id}/group_id/{group_id}/tables/{database_name}/{table_name}` |
| inventory_groups.py | DELETE | `/cluster/{cluster_id}/inventory-groups/{group_id}` | `/inventory/cluster/{cluster_id}/group_id/{group_id}` |

**2. Restore/prune stay under the `backup` domain** (`/backup/manual/restore/...`, `/backup/manual/prune/...`) rather than becoming their own top-level domains. Rationale: they act on the same backup repository/snapshot resources as full/incremental backup, and the renamed section ("Manual Backups") is the natural home for every manual, synchronous-submission-of-an-async-job operation. Alternative considered: separate `/restore` and `/prune` domains — rejected because it would fragment one conceptual section (job submission against a cluster's backups) into three router-level path roots for no behavioral reason.

**3. `GET /job/{job_id}` is left unchanged.** It polls status for a job of *any* type (backup, restore, or prune), so nesting it under `/backup/manual/...` would misrepresent it, and it already satisfies the convention as domain-first with no required context. Alternative considered: `/backup/manual/job/{id}` — rejected per user decision (see conversation).

**4. Collection vs. single-resource domain split for inventory** (`/inventories/...` plural for the collection, `/inventory/...` singular for one group and its sub-resources, including the two-key table-membership endpoints) follows the proposal's example literally and mirrors the plural/singular convention already used elsewhere in this API (`/cluster` vs `/clusters`).

**5. Trailing single-identifier segments get a label** (`.../schedule_id/{schedule_id}`, `.../name/{name}`, `.../group_id/{group_id}`), matching the proposal's own `group_id/{group_id}` example. The two-part table-membership identifier (`{database_name}/{table_name}`) keeps its existing bare, unlabeled form — per user decision, since `/tables/` already establishes what the following two segments are and no other multi-segment identifier in this API is labeled.

**6. No compatibility aliases.** Confirmed by grep that no deprecation/alias pattern exists anywhere in `api/` today, so there is no precedent to preserve and the proposal's "no aliases" default applies without exception.

**7. Router tags.** `jobs.router`'s `tags=["jobs"]` becomes `tags=["manual-backups"]` to match the renamed OpenAPI section; `schedules`, `repositories`, `inventory-groups` tags stay as-is (they already name the right domain, only the paths were wrong).

## Risks / Trade-offs

- **[Risk] Every existing external caller of the four routers' old paths breaks immediately, with no transition window.** → Mitigation: none needed beyond documentation — proposal.md already states this is intentional (no aliasing mechanism exists in this project, and none is being introduced). Communicate via the docs update and changelog/PR description.
- **[Risk] Missing a call site during the mechanical rename leaves a dangling reference to an old path (in `cli_api/*.py`, docs, or a test) that only surfaces at runtime or in CI.** → Mitigation: tasks.md includes an explicit "grep for old path fragments" verification step as the last task, run after all edits.
- **[Risk] `docs/api.md` currently has no "Inventory Groups" section at all (pre-existing gap, unrelated to this change).** → Mitigation: tasks.md adds writing that section using the new paths directly, so we don't first document the old paths and then immediately rename them.

## Migration Plan

Single-step cutover, no rollback path needed beyond `git revert` (no data migration, no persisted state references these paths — they're referenced live in HTTP calls only):

1. Update route decorators in the four route files.
2. Update `cli_api` client call sites to match.
3. Update tests to the new paths.
4. Update `docs/api.md`, `docs/commands.md`, `docs/scheduling.md`.
5. Run the full test suite; grep for old path fragments across the repo (excluding `openspec/changes/archive/`) to confirm none remain.
