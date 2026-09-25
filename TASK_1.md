# TASK 1: Inventory Group CRUD API

## Context

`table_inventory` ("groups") is a table that lives inside each target StarRocks cluster's `ops`
schema (DDL: `src/starrocks_br/schema.py:125-138`, `get_table_inventory_schema`):

```sql
CREATE TABLE IF NOT EXISTS {ops_database}.table_inventory (
    inventory_group STRING NOT NULL,
    database_name STRING NOT NULL,
    table_name STRING NOT NULL,   -- or '*' wildcard for all tables in database
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
UNIQUE KEY (inventory_group, database_name, table_name)
DISTRIBUTED BY HASH(inventory_group)
```

Today there is **no REST API and no CLI CRUD command** for it. The only ways to populate it are:
a one-shot config-driven bootstrap insert (`schema.bootstrap_table_inventory`, which uses raw
f-string SQL interpolation — a SQL-injection risk), or manual SQL run directly against the cluster
by an admin.

Separately, `POST /clusters/{cluster_id}/backups/full` and `.../backups/incremental` currently
accept requests with no `group` at all (schema field is `group: str | None = None`), return
**202**, and only fail later, asynchronously, with a cryptic `KeyError` (`Job.error_message =
"'group'"`) once the job handler hits `params["group"]`. There is no "back up everything" fallback
anywhere in the codebase — this is purely a bug in error handling, not a missing feature.

This task adds a proper CRUD API for inventory groups and fixes the missing-group failure mode to
fail fast and clearly.

## Design decisions

1. **No "backup everything" fallback.** `group` stays required for `backup_full`/`backup_incremental`.
   Inventing an implicit "back up all tables" default now would be a dangerous, undiscussed
   scope-widening change for a backup tool — better to force callers to be explicit. Once groups are
   a discoverable, manageable resource (this task), requiring a real, pre-existing group is the
   coherent contract.
2. **Validate synchronously, not just via the DB unique key.** Group existence must be checked with a
   single fast SQL lookup on the request thread before the job is enqueued, returning 404 immediately.
   This eliminates the async `KeyError` failure mode for the "no group" / "unknown group" cases.
3. **Groups live on the cluster, so the API mirrors `repositories.py`**, not `clusters.py`. Routes are
   a thin HTTP/error-translation layer over a new domain module; the domain module does the SQL work.
4. **Deduplicate the `_connect`/`_connect_or_503`/`_get_cluster_or_404` trio** before adding a third
   copy of it (it's already duplicated across `repositories.py`, `jobs.py`, `clusters.py`).

## Step-by-step implementation

### 1. Shared connection helper (deduplication)

Create `src/starrocks_br/api/routes/_cluster_connect.py`:

```python
def get_cluster_or_404(db: Session, cluster_id: int) -> Cluster: ...
def connect(cluster: Cluster) -> db_module.StarRocksDB: ...
def connect_or_503(cluster: Cluster) -> db_module.StarRocksDB: ...
```

Move the existing bodies verbatim from `src/starrocks_br/api/routes/repositories.py` (`_connect`,
`_connect_or_503`, lines ~45-69) and the `_get_cluster_or_404` helper duplicated in `repositories.py`,
`jobs.py:29-33`, and `clusters.py`. Update all three existing route modules to import from the new
shared module instead of defining their own copies. Run the existing test suite
(`pytest tests/test_api_clusters.py tests/test_api_repositories.py tests/test_api_jobs.py`) to confirm
no behavior change before proceeding.

### 2. Domain module: `src/starrocks_br/inventory_groups.py`

Mirrors the shape of `src/starrocks_br/repository.py` (SQL-building + `StarRocksDB` calls, no HTTP
knowledge). Use `utils.quote_value`/`utils.quote_identifier` for every interpolated value — do not
repeat `schema.bootstrap_table_inventory`'s raw-interpolation pattern.

```python
class InventoryGroupNotFoundError(RuntimeError):
    """Raised when an inventory group has no rows in table_inventory."""

class InventoryMembershipConflictError(RuntimeError):
    """Raised when inserting a (group, database, table) row that already exists."""

class InventoryMembershipNotFoundError(RuntimeError):
    """Raised when removing a (group, database, table) row that does not exist."""


def list_groups(db, ops_database: str = "ops") -> list[dict]:
    """SELECT inventory_group, COUNT(*) FROM {ops_database}.table_inventory
    GROUP BY inventory_group ORDER BY inventory_group.
    Returns [{"name": str, "table_count": int}, ...]."""

def group_exists(db, group_name: str, ops_database: str = "ops") -> bool:
    """SELECT 1 FROM {ops_database}.table_inventory
    WHERE inventory_group = quote_value(group_name) LIMIT 1."""

def get_group(db, group_name: str, ops_database: str = "ops") -> list[dict]:
    """SELECT database_name, table_name, created_at, updated_at
    FROM {ops_database}.table_inventory WHERE inventory_group = quote_value(group_name)
    ORDER BY database_name, table_name.
    Returns [] if the group has no rows (caller decides whether that means 404)."""

def add_membership(db, group_name: str, database_name: str, table_name: str,
                    ops_database: str = "ops") -> dict:
    """INSERT INTO {ops_database}.table_inventory (inventory_group, database_name, table_name)
    VALUES (quote_value(group_name), quote_value(database_name), quote_value(table_name)).
    Catch the duplicate-key error from StarRocks and re-raise as
    InventoryMembershipConflictError. Returns {"group", "database", "table"}."""

def add_memberships_bulk(db, group_name: str, entries: list[tuple[str, str]],
                          ops_database: str = "ops") -> list[dict]:
    """Loop add_membership per (database, table) entry. Matches existing
    bootstrap_table_inventory idempotency: partial success across the loop is acceptable
    since the UNIQUE KEY dedups on retry."""

def remove_membership(db, group_name: str, database_name: str, table_name: str,
                       ops_database: str = "ops") -> None:
    """SELECT first to confirm the row exists (raise InventoryMembershipNotFoundError if not),
    then DELETE FROM {ops_database}.table_inventory
    WHERE inventory_group = ... AND database_name = ... AND table_name = ..."""

def delete_group(db, group_name: str, ops_database: str = "ops") -> int:
    """Raise InventoryGroupNotFoundError if group_exists() is False.
    Otherwise DELETE FROM {ops_database}.table_inventory WHERE inventory_group = ...
    Return the row count deleted (from a preceding COUNT(*), not DELETE result metadata)."""
```

`table_name` validation: allow any non-empty string, `'*'` is the documented wildcard for "all
tables in database" — no extra validation needed beyond `Field(min_length=1)` at the schema layer.

### 3. New schemas in `src/starrocks_br/api/schemas.py`

Follow the `RepositoryCreate`/`RepositoryRead` pattern — plain dict serialization, no
`ConfigDict(from_attributes=True)`, since this data is not ORM-backed:

```python
class InventoryGroupSummary(BaseModel):
    name: str
    table_count: int

class InventoryMembershipCreate(BaseModel):
    database: str = Field(min_length=1, max_length=128)
    table: str = Field(min_length=1, max_length=128,
                        description="Table name, or '*' for all tables in the database")

class InventoryGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    tables: list[InventoryMembershipCreate] = Field(min_length=1)

class InventoryMembershipRead(BaseModel):
    database: str
    table: str
    created_at: str
    updated_at: str

class InventoryGroupRead(BaseModel):
    name: str
    tables: list[InventoryMembershipRead]
```

### 4. New router: `src/starrocks_br/api/routes/inventory_groups.py`

```python
router = APIRouter(tags=["inventory-groups"], dependencies=[Depends(require_api_key)])
```

All endpoints scoped under `/clusters/{cluster_id}/inventory-groups` (matches the
`/clusters/{cluster_id}/repositories` convention):

| Method | Path | Function | Status | Behavior |
|---|---|---|---|---|
| GET | `/clusters/{cluster_id}/inventory-groups` | `list_inventory_groups` | 200 | `response_model=list[InventoryGroupSummary]` |
| POST | `/clusters/{cluster_id}/inventory-groups` | `create_inventory_group` | 201 | body `InventoryGroupCreate`; bulk-inserts; 409 if the group name already has any rows |
| GET | `/clusters/{cluster_id}/inventory-groups/{group_name}` | `get_inventory_group` | 200 | `response_model=InventoryGroupRead`; 404 if no rows |
| POST | `/clusters/{cluster_id}/inventory-groups/{group_name}/tables` | `add_inventory_group_table` | 201 | body `InventoryMembershipCreate`; 409 on duplicate; `response_model=InventoryMembershipRead` |
| DELETE | `/clusters/{cluster_id}/inventory-groups/{group_name}/tables/{database}/{table}` | `remove_inventory_group_table` | 204 | 404 if membership not found |
| DELETE | `/clusters/{cluster_id}/inventory-groups/{group_name}` | `delete_inventory_group` | 204 | 404 if group has zero rows |

Route bodies follow `repositories.py`'s shape exactly:

```python
cluster = get_cluster_or_404(db, cluster_id)
database = connect_or_503(cluster)
try:
    ...  # call inventory_groups.* functions, translate exceptions to HTTPException
finally:
    database.close()
```

Exception translation: `InventoryGroupNotFoundError` → 404, `InventoryMembershipConflictError` →
409, `InventoryMembershipNotFoundError` → 404.

Register the router in `src/starrocks_br/api/app.py`:
```python
from .routes import inventory_groups
app.include_router(inventory_groups.router)
```

### 5. Fix `schema.bootstrap_table_inventory`

In `src/starrocks_br/schema.py:93-122`:
- Replace raw f-string interpolation of `group`/`database`/`table` (and the `SHOW DATABASES LIKE
  '{database_name}'` calls in the same function) with `utils.quote_value`.
- Recommended: delegate row insertion to `inventory_groups.add_membership`, catching
  `InventoryMembershipConflictError` and treating it as a no-op, to preserve today's idempotent
  "re-running init only adds new rows" behavior.
- No DDL/schema change — `get_table_inventory_schema` is untouched.

### 6. Group-existence check in `jobs.py`

In `src/starrocks_br/api/routes/jobs.py`, before enqueueing `backup_full`/`backup_incremental`:

```python
from ... import inventory_groups

def _submit_backup_job(db, cluster_id, job_type, payload):
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
```

Route this through `submit_backup_full`/`submit_backup_incremental` instead of the generic `_submit`.
(If TASK_2 is implemented in the same change, write this check once — see TASK_2.md's note on this
dependency.)

### 7. Harden `handlers.py` (defense in depth)

In `src/starrocks_br/jobs/handlers.py`, change `run_backup_full`/`run_backup_incremental`'s
`group = params["group"]` to:

```python
group = params.get("group")
if not group:
    raise ValueError("'group' is required for backup_full")  # or backup_incremental
```

This ensures any non-HTTP caller (tests, a future backend) gets a clear message in
`Job.error_message` instead of the cryptic `"'group'"` `KeyError` string.

### 8. Tests

New file `tests/test_inventory_groups_sql.py` (mirrors `tests/test_repository_sql.py`): unit tests
against mocked `db.query`/`db.execute` for each `inventory_groups.py` function — correct
grouping/counting in `list_groups`, correct dict shape from `get_group`, `add_membership` raising
`InventoryMembershipConflictError` on duplicate-key error text, `remove_membership`/`delete_group`
raising not-found errors when absent, and assertions that all SQL is built via
`quote_value`/`quote_identifier` (inspect `db.execute.call_args`/`db.query.call_args`).

New file `tests/test_api_inventory_groups.py` (mirrors `tests/test_api_repositories.py`'s fake-DB /
`_patch_connect` pattern):
- `test_list_inventory_groups_success`
- `test_create_inventory_group_success` (201)
- `test_create_inventory_group_duplicate_is_409`
- `test_get_inventory_group_success`
- `test_get_unknown_inventory_group_is_404`
- `test_add_table_to_group_success` (201)
- `test_add_duplicate_table_to_group_is_409`
- `test_remove_table_from_group_success` (204)
- `test_remove_nonexistent_table_from_group_is_404`
- `test_delete_inventory_group_success` (204)
- `test_delete_nonexistent_inventory_group_is_404`
- `test_inventory_groups_against_unknown_cluster_is_404`
- `test_inventory_groups_unreachable_cluster_is_503`

Updates to existing tests:
- `tests/test_api_jobs.py`: add `test_submit_backup_full_unknown_group_is_404` and
  `test_submit_backup_incremental_unknown_group_is_404` (monkeypatch `inventory_groups.group_exists`
  to `False`); update existing happy-path backup_full/backup_incremental tests to monkeypatch
  `group_exists` to `True` (the new check now runs before `submit_job`).
- `tests/test_jobs_handlers.py`: assert `run_backup_full`/`run_backup_incremental` raise a clear
  `ValueError` (not `KeyError`) when `group` is absent from `params`.
- Add or extend a `tests/test_schema.py` covering `bootstrap_table_inventory`: assert a group name
  containing a single quote (e.g. `o'brien_group`) is safely inserted via `quote_value` rather than
  breaking the SQL string.

## Verification

```
pytest tests/test_api_clusters.py tests/test_api_repositories.py tests/test_api_jobs.py \
       tests/test_jobs_handlers.py tests/test_inventory_groups_sql.py \
       tests/test_api_inventory_groups.py tests/test_schema.py
```

Manually via `/docs` (FastAPI Swagger UI) or `curl`:
1. `POST /clusters/{id}/inventory-groups` with `{"name": "prod", "tables": [{"database": "mydb", "table": "*"}]}` → 201.
2. `GET /clusters/{id}/inventory-groups` → shows `prod` with `table_count: 1`.
3. `POST /clusters/{id}/backups/full` with `{"group": "does_not_exist"}` → 404, not 202.
4. `POST /clusters/{id}/backups/full` with `{"group": "prod"}` → 202, job eventually succeeds.

## Critical files

- `src/starrocks_br/inventory_groups.py` (new)
- `src/starrocks_br/api/routes/inventory_groups.py` (new)
- `src/starrocks_br/api/routes/_cluster_connect.py` (new)
- `src/starrocks_br/api/schemas.py`
- `src/starrocks_br/api/routes/jobs.py`
- `src/starrocks_br/api/routes/repositories.py`
- `src/starrocks_br/api/routes/clusters.py`
- `src/starrocks_br/api/app.py`
- `src/starrocks_br/schema.py`
- `src/starrocks_br/jobs/handlers.py`
