# TASK 2: Split the Job Submission Payload Per Endpoint

## Context

All four job-submission endpoints — `POST /clusters/{cluster_id}/backups/full`,
`.../backups/incremental`, `.../restores`, `.../prunes` — currently share one Pydantic model,
`JobSubmitRequest` (`src/starrocks_br/api/schemas.py:66-78`):

```python
class JobSubmitRequest(BaseModel):
    group: str | None = None
    name: str | None = None
    baseline_backup: str | None = None
    target_label: str | None = None
    table: str | None = None
    rename_suffix: str = "_restored"
    keep_last: int | None = None
    older_than: str | None = None
    snapshot: str | None = None
    snapshots: str | None = None
    dry_run: bool = False
    backend: str | None = None
```

Each endpoint's OpenAPI schema therefore advertises fields it ignores — e.g. `/backups/full`
exposes `keep_last`, `snapshot`, `target_label`, none of which it uses. This is confusing and lets
callers send fields that silently do nothing. The goal is one dedicated request model per endpoint,
containing only the fields that endpoint actually consumes, so each endpoint's schema is
self-explanatory.

**Confirmed field usage per endpoint** (read directly from `src/starrocks_br/jobs/handlers.py`):

- `run_backup_full` (`handlers.py:78-133`): `group` (required, currently `params["group"]`), `name`
  (optional).
- `run_backup_incremental` (`handlers.py:136-190`): `group` (required), `name` (optional),
  `baseline_backup` (optional).
- `run_restore` (`handlers.py:193-235`): `target_label` (required), `group` and `table` (both
  optional; **at most one** may be set — `if group and table: raise ValueError(...)` at
  `handlers.py:199-200`; if **neither** is set, all tables in the backup are restored — this is a
  valid, currently-supported call shape, not an error), `rename_suffix` (optional, default
  `"_restored"`).
- `run_prune` (`handlers.py:238-293`): `group` (optional — scopes which backups are eligible for
  pruning, passed to `prune.get_successful_backups(..., group=group, ...)`), and **exactly one** of
  `keep_last` / `older_than` / `snapshot` / `snapshots` (`handlers.py:248-252`), plus `dry_run`
  (optional, default `False`).
- `backend` is accepted by all four but is never part of job `params` — it's excluded via
  `payload.model_dump(exclude={"backend"}, ...)` in `jobs.py:76` and used only to pick the execution
  backend.

Note `prune` uses `group` too — this is easy to miss and must not be dropped from `PruneRequest`.

## Design decisions

1. **Four models, not one.** `BackupFullRequest`, `BackupIncrementalRequest`, `RestoreRequest`,
   `PruneRequest` — each with exactly the fields listed above.
2. **`extra="forbid"` on every model.** By default Pydantic silently ignores unknown fields; that
   defeats the goal ("endpoints must be self-explanatory, no field bleed from other endpoints"). Set
   `model_config = ConfigDict(extra="forbid")` so posting e.g. `keep_last` to `/backups/full` is a
   422, not a silent no-op.
3. **`group` becomes genuinely required (`group: str`, no default) for `BackupFullRequest` and
   `BackupIncrementalRequest`.** Combined with TASK_1's group-existence check, a missing or unknown
   group is now caught before the job is ever created — no more async `KeyError` failures.
4. **Validation moves from `handlers.py` runtime `ValueError`s into Pydantic model validators**,
   giving synchronous 422 responses instead of deferred async job failures — but the `handlers.py`
   checks are **kept**, not deleted, as defense-in-depth for any future non-HTTP caller of the same
   handler functions.
5. **`RestoreRequest`'s rule is "at most one of group/table", not "exactly one".** Both may be
   omitted today (restores everything in the backup) — do not change this behavior when moving the
   check earlier.
6. **Remove `JobSubmitRequest` entirely once the four models exist.** No deprecated alias — this is
   an internal API, not a published SDK, and the repo convention is no backwards-compatibility
   shims.

## Step-by-step implementation

### 1. New schemas in `src/starrocks_br/api/schemas.py`

Add `model_validator` to the existing `pydantic` import, then add:

```python
class BackupFullRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group: str = Field(min_length=1, max_length=128)
    name: str | None = None
    backend: str | None = None


class BackupIncrementalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group: str = Field(min_length=1, max_length=128)
    name: str | None = None
    baseline_backup: str | None = None
    backend: str | None = None


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_label: str = Field(min_length=1)
    group: str | None = None
    table: str | None = None
    rename_suffix: str = "_restored"
    backend: str | None = None

    @model_validator(mode="after")
    def _check_group_and_table_not_both_set(self) -> "RestoreRequest":
        if self.group and self.table:
            raise ValueError("Cannot specify both 'group' and 'table'")
        return self


class PruneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group: str | None = None
    keep_last: int | None = Field(default=None, gt=0)
    older_than: str | None = None
    snapshot: str | None = None
    snapshots: str | None = None
    dry_run: bool = False
    backend: str | None = None

    @model_validator(mode="after")
    def _check_exactly_one_strategy(self) -> "PruneRequest":
        specified = [v for v in (self.keep_last, self.older_than, self.snapshot, self.snapshots)
                     if v is not None]
        if len(specified) != 1:
            raise ValueError(
                "Exactly one of 'keep_last', 'older_than', 'snapshot', 'snapshots' must be provided"
            )
        return self
```

Delete the `JobSubmitRequest` class.

### 2. Update `src/starrocks_br/api/routes/jobs.py`

- Change the import: `from ..schemas import BackupFullRequest, BackupIncrementalRequest, JobRead,
  PruneRequest, RestoreRequest`.
- `_submit` no longer needs `exclude_none=True` (each model now only carries fields relevant to its
  endpoint, so `None` for a genuinely optional field is a meaningful signal, and every handler
  already reads optional fields via `.get()`, which behaves the same whether the key is absent or
  present with value `None`):

  ```python
  def _submit(db: Session, cluster_id: int, job_type: str, payload: BaseModel) -> Job:
      cluster = get_cluster_or_404(db, cluster_id)
      params = payload.model_dump(exclude={"backend"})
      return submit_job(db, cluster, job_type, params, payload.backend)
  ```

- For `backup_full`/`backup_incremental`, route through a dedicated helper that also performs
  TASK_1's group-existence check (write this once — do not duplicate the check if TASK_1 is
  implemented separately; if TASK_1 lands first, just reuse its version of this helper):

  ```python
  from ... import inventory_groups

  def _submit_backup_job(
      db: Session, cluster_id: int, job_type: str,
      payload: BackupFullRequest | BackupIncrementalRequest,
  ) -> Job:
      cluster = get_cluster_or_404(db, cluster_id)
      database = connect_or_503(cluster)
      try:
          if not inventory_groups.group_exists(database, payload.group, cluster.ops_database):
              raise HTTPException(
                  status_code=404,
                  detail=f"Inventory group '{payload.group}' not found on cluster '{cluster.name}'",
              )
      finally:
          database.close()
      return submit_job(db, cluster, job_type, payload.model_dump(exclude={"backend"}), payload.backend)


  @router.post("/clusters/{cluster_id}/backups/full", response_model=JobRead,
               status_code=status.HTTP_202_ACCEPTED)
  def submit_backup_full(cluster_id: int, payload: BackupFullRequest, db: Session = Depends(get_db)) -> Job:
      return _submit_backup_job(db, cluster_id, "backup_full", payload)


  @router.post("/clusters/{cluster_id}/backups/incremental", response_model=JobRead,
               status_code=status.HTTP_202_ACCEPTED)
  def submit_backup_incremental(cluster_id: int, payload: BackupIncrementalRequest, db: Session = Depends(get_db)) -> Job:
      return _submit_backup_job(db, cluster_id, "backup_incremental", payload)


  @router.post("/clusters/{cluster_id}/restores", response_model=JobRead,
               status_code=status.HTTP_202_ACCEPTED)
  def submit_restore(cluster_id: int, payload: RestoreRequest, db: Session = Depends(get_db)) -> Job:
      return _submit(db, cluster_id, "restore", payload)


  @router.post("/clusters/{cluster_id}/prunes", response_model=JobRead,
               status_code=status.HTTP_202_ACCEPTED)
  def submit_prune(cluster_id: int, payload: PruneRequest, db: Session = Depends(get_db)) -> Job:
      return _submit(db, cluster_id, "prune", payload)
  ```

### 3. Keep `handlers.py` runtime checks as defense-in-depth

Do **not** remove:
- `run_restore`'s `if group and table: raise ValueError("Cannot specify both 'group' and 'table'")`
  (`handlers.py:199-200`).
- `run_prune`'s exactly-one-strategy check (`handlers.py:248-252`).

These protect any future direct/non-HTTP caller of the handler functions (e.g. a different job
backend). The Pydantic validators added in step 1 only add an earlier, synchronous 422 at the HTTP
layer — they do not replace the handler-level checks.

### 4. Tests

Update `tests/test_api_jobs.py`:
- Existing valid-payload tests keep the same field names/values; they need
  `inventory_groups.group_exists` monkeypatched to `True` for backup_full/backup_incremental tests
  now that the existence check runs before job creation.
- Add `test_submit_backup_full_missing_group_is_422` — POST `{}` (no `group`) → 422 (was: 202 +
  async failure).
- Add `test_submit_backup_full_rejects_foreign_field_is_422` — POST `{"group": "g1", "keep_last": 5}`
  → 422 (extra field forbidden).
- Add `test_submit_restore_both_group_and_table_is_422`.
- Add `test_submit_restore_neither_group_nor_table_succeeds` — confirms restoring "everything in the
  backup" still works (this must NOT become a 422 — preserving current behavior is required).
- Add `test_submit_prune_no_strategy_is_422`.
- Add `test_submit_prune_two_strategies_is_422`.
- Add `test_submit_prune_extra_field_is_422` — e.g. POST `{"snapshot": "x", "table": "t"}` → 422.

No changes needed to `tests/test_jobs_handlers.py` — the handler-level `ValueError` checks and their
existing tests are unchanged (step 3).

Grep the repo for other references to `JobSubmitRequest` (README, `docs/`, any example payloads) and
update them to reference the new per-endpoint models.

## Verification

```
pytest tests/test_api_jobs.py tests/test_jobs_handlers.py
```

Manually via `/docs` (FastAPI Swagger UI):
1. Confirm `/backups/full`'s request schema lists only `group`, `name`, `backend` — no
   `keep_last`/`snapshot`/`target_label`/etc.
2. Confirm `/prunes`'s request schema lists `group`, `keep_last`, `older_than`, `snapshot`,
   `snapshots`, `dry_run`, `backend` — no `target_label`/`rename_suffix`/etc.
3. POST `{}` to `/backups/full` → 422 with a clear "field required" message for `group`.
4. POST `{"target_label": "x", "group": "g1", "table": "t1"}` to `/restores` → 422 ("Cannot specify
   both...").

## Critical files

- `src/starrocks_br/api/schemas.py`
- `src/starrocks_br/api/routes/jobs.py`
- `src/starrocks_br/jobs/handlers.py` (read-only reference; no changes beyond TASK_1's hardening)
- `tests/test_api_jobs.py`

## Sequencing note

This task and TASK_1.md both touch `jobs.py`'s backup_full/backup_incremental submission path (the
group-existence check). Implement TASK_1 first, then layer TASK_2's schema split on top and reuse
TASK_1's `_submit_backup_job` helper rather than writing the check twice. If done in the reverse
order, write the check once in TASK_2's version and have TASK_1 reuse it instead.
