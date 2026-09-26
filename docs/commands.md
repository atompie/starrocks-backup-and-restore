# Command Reference

Detailed reference for all StarRocks Backup & Restore commands.

## init

Initialize the ops database and control tables.

### Syntax

```bash
starrocks-br init --config <config_file>
```

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `--config` | Yes | Path to configuration file |

### What It Creates

| Table | Purpose |
|-------|---------|
| `ops.table_inventory` | Inventory groups (collections of tables) |
| `ops.backup_history` | Backup operation log |
| `ops.restore_history` | Restore operation log |
| `ops.run_status` | Job concurrency control |
| `ops.backup_partitions` | Partition-level backup details |

### Example

```bash
starrocks-br init --config config.yaml
```

### After Initialization

Populate the inventory with your backup groups:

```sql
INSERT INTO ops.table_inventory (inventory_group, database_name, table_name)
VALUES
  ('my_group', 'production_db', 'users'),
  ('my_group', 'production_db', 'orders');
```

## backup full

Run a full backup of all tables in an inventory group.

### Syntax

```bash
starrocks-br backup full --config <config_file> --group <group_name> [--name <label>]
```

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `--config` | Yes | Path to configuration file |
| `--group` | Yes | Inventory group to backup |
| `--name` | No | Custom backup label. Supports `-v#r` placeholder for auto-versioning |

### Examples

**Basic full backup:**
```bash
starrocks-br backup full --config config.yaml --group production_tables
```

**With custom label:**
```bash
starrocks-br backup full --config config.yaml --group production_tables --name my_backup_v1
```

### Monitoring

```sql
-- Active backups
SHOW BACKUP;

-- Backup history
SELECT label, status, started_at, finished_at
FROM ops.backup_history
ORDER BY started_at DESC
LIMIT 10;
```

## backup incremental

Backup only partitions that changed since the last full backup.

### Syntax

```bash
starrocks-br backup incremental --config <config_file> --group <group_name> [--baseline-backup <label>] [--name <label>]
```

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `--config` | Yes | Path to configuration file |
| `--group` | Yes | Inventory group to backup |
| `--baseline-backup` | No | Specific full backup to use as baseline (default: most recent) |
| `--name` | No | Custom backup label. Supports `-v#r` placeholder for auto-versioning |

### Examples

**Basic incremental backup:**
```bash
starrocks-br backup incremental --config config.yaml --group production_tables
```

**With specific baseline:**
```bash
starrocks-br backup incremental \
  --config config.yaml \
  --group production_tables \
  --baseline-backup sales_db_20251118_full
```

**With custom label:**
```bash
starrocks-br backup incremental \
  --config config.yaml \
  --group production_tables \
  --name my_incremental_v1
```

### Requirements

- Must have at least one successful full backup for the group
- Works best with partitioned tables

## restore

Restore data from a backup with automatic backup chain resolution.

### Syntax

```bash
starrocks-br restore --config <config_file> --target-label <backup_label> [--group <group_name>] [--table <table_name>] [--rename-suffix <suffix>] [--yes]
```

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `--config` | Yes | Path to configuration file |
| `--target-label` | Yes | Backup label to restore from |
| `--group` | No | Restore only this inventory group |
| `--table` | No | Restore only this table (name only, no database prefix) |
| `--rename-suffix` | No | Suffix for temp tables (default: `_restored`) |
| `--yes` | No | Skip confirmation prompt |

**Note:** Cannot use both `--group` and `--table` together.

### Examples

**Full restore:**
```bash
starrocks-br restore --config config.yaml --target-label sales_db_20251118_full
```

**Group-based restore:**
```bash
starrocks-br restore \
  --config config.yaml \
  --target-label sales_db_20251118_full \
  --group critical_tables
```

**Single table restore:**
```bash
starrocks-br restore \
  --config config.yaml \
  --target-label sales_db_20251118_full \
  --table orders
```

**Skip confirmation:**
```bash
starrocks-br restore \
  --config config.yaml \
  --target-label sales_db_20251118_full \
  --yes
```

### Finding Available Backups

```sql
SELECT label, backup_type, finished_at
FROM ops.backup_history
WHERE status = 'SUCCESS'
ORDER BY finished_at DESC;
```

### Monitoring

```sql
-- Active restores
SHOW RESTORE;

-- Restore history
SELECT restore_label, target_backup, status, started_at
FROM ops.restore_history
ORDER BY started_at DESC
LIMIT 10;
```

## prune

Delete old backups to manage repository storage using various pruning strategies.

### Syntax

```bash
starrocks-br prune --config <config_file> [--group <group_name>] [STRATEGY] [--dry-run] [--yes]
```

### Pruning Strategies

You must specify exactly ONE of the following strategies:

| Strategy | Description |
|----------|-------------|
| `--keep-last N` | Keep only the N most recent backups, delete the rest |
| `--older-than TIMESTAMP` | Delete backups older than the specified timestamp |
| `--snapshot LABEL` | Delete a specific backup by label |
| `--snapshots LABEL1,LABEL2,...` | Delete multiple specific backups (comma-separated) |

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `--config` | Yes | Path to configuration file |
| `--group` | No | Only prune backups for this inventory group |
| `--keep-last` | Strategy | Keep N most recent backups (must be positive integer) |
| `--older-than` | Strategy | Delete backups older than this timestamp (format: `YYYY-MM-DD HH:MM:SS`) |
| `--snapshot` | Strategy | Delete this specific backup label |
| `--snapshots` | Strategy | Delete these specific backup labels (comma-separated) |
| `--dry-run` | No | Show what would be deleted without actually deleting |
| `--yes` | No | Skip confirmation prompt |

### Examples

**Keep only last 5 backups:**
```bash
starrocks-br prune --config config.yaml --keep-last 5
```

**Keep last 3 backups for a specific group:**
```bash
starrocks-br prune --config config.yaml --group production_tables --keep-last 3
```

**Delete backups older than a specific date:**
```bash
starrocks-br prune --config config.yaml --older-than "2024-01-01 00:00:00"
```

**Delete a specific backup:**
```bash
starrocks-br prune --config config.yaml --snapshot sales_db_20240101_full
```

**Delete multiple specific backups:**
```bash
starrocks-br prune --config config.yaml --snapshots backup1,backup2,backup3
```

**Dry run to preview deletion:**
```bash
starrocks-br prune --config config.yaml --keep-last 5 --dry-run
```

**Auto-confirm deletion:**
```bash
starrocks-br prune --config config.yaml --keep-last 5 --yes
```

### Workflow

1. **Lists snapshots**: Queries successful backups from `ops.backup_history`
2. **Filters by strategy**: Determines which backups to delete based on your chosen strategy
3. **Shows preview**: Displays what will be deleted (unless `--yes` is used)
4. **Confirms**: Asks for confirmation (unless `--yes` or `--dry-run`)
5. **Deletes snapshots**: Executes `DROP SNAPSHOT` for each backup
6. **Cleans history**: Removes entries from `ops.backup_history` and `ops.backup_partitions`

### Finding Backups to Prune

```sql
-- List all successful backups
SELECT label, finished_at,
       DATEDIFF(NOW(), finished_at) as days_old
FROM ops.backup_history
WHERE status = 'FINISHED'
ORDER BY finished_at DESC;

-- Count backups per group
SELECT ti.inventory_group, COUNT(DISTINCT bh.label) as backup_count
FROM ops.backup_history bh
JOIN ops.backup_partitions bp ON bh.label = bp.label
JOIN ops.table_inventory ti ON bp.database_name = ti.database_name
WHERE bh.status = 'FINISHED'
GROUP BY ti.inventory_group;
```

### Important Notes

- **Irreversible**: Pruning permanently deletes backups from the repository
- **Group filtering**: Use `--group` to prune only specific backup groups
- **Dry run first**: Always test with `--dry-run` before actual deletion
- **Keep-last counts**: Sorted by `finished_at` timestamp (oldest deleted first)
- **Timestamp format**: Must be `YYYY-MM-DD HH:MM:SS` (24-hour format)

### Testing Integration

For integration testing, you can:

1. **Create test backups:**
```bash
starrocks-br backup full --config config.yaml --group test_group --name test_backup_1
starrocks-br backup full --config config.yaml --group test_group --name test_backup_2
starrocks-br backup full --config config.yaml --group test_group --name test_backup_3
```

2. **Verify backups exist:**
```sql
SELECT label FROM ops.backup_history WHERE status = 'FINISHED' ORDER BY finished_at;
```

3. **Test pruning with dry-run:**
```bash
starrocks-br prune --config config.yaml --keep-last 1 --dry-run
```

4. **Execute prune:**
```bash
starrocks-br prune --config config.yaml --keep-last 1 --yes
```

5. **Verify deletion:**
```sql
-- Should only show 1 backup remaining
SELECT label FROM ops.backup_history WHERE status = 'FINISHED';

-- Verify snapshot was dropped from repository
SHOW SNAPSHOT ON your_repository;
```

## API Server

Everything the direct CLI commands above do is also available over HTTP through an optional
FastAPI server, plus cluster registration and scheduling, which the direct CLI does not have.
See the **[full API Server guide](api.md)** for the complete endpoint/CLI reference, running the
server, and troubleshooting — this section is a quickstart.
See [Configuration Reference](configuration.md#api-server-configuration) for the required
environment variables.

**Install and start:**
```bash
pip install "starrocks-br[api]"
export STARROCKS_BR_API_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
export STARROCKS_BR_DB_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
starrocks-br api serve --host 0.0.0.0 --port 8000
```

Every request below needs `Authorization: Bearer $STARROCKS_BR_API_KEY`. The CLI's `api`
subcommands read the same key from `--api-key` or `STARROCKS_BR_API_KEY`, plus a
`--api-url`/`STARROCKS_BR_API_URL` pointing at the server.

### Register a cluster

```bash
curl -X POST http://localhost:8000/clusters \
  -H "Authorization: Bearer $STARROCKS_BR_API_KEY" -H "Content-Type: application/json" \
  -d '{"name": "prod-eu", "host": "sr.internal", "port": 9030, "user": "root",
       "password": "secret", "database": "mydb", "repository": "s3_repo"}'

# or via the CLI
starrocks-br api cluster add --name prod-eu --host sr.internal --port 9030 \
  --user root --password secret --database mydb --repository s3_repo
starrocks-br api cluster list
```

### Submit and poll a job

```bash
curl -X POST http://localhost:8000/cluster/1/backups/full \
  -H "Authorization: Bearer $STARROCKS_BR_API_KEY" -H "Content-Type: application/json" \
  -d '{"group": "production"}'
# -> 202 {"id": 1, "status": "PENDING", ...}

curl http://localhost:8000/job/1 -H "Authorization: Bearer $STARROCKS_BR_API_KEY"
# -> {"status": "RUNNING", "progress_pct": 42, ...} or {"status": "SUCCESS", ...}

# or via the CLI (--wait polls until the job finishes)
starrocks-br api job submit --cluster 1 --type backup-full --group production --wait
```

The same pattern applies to `backups/incremental`, `restores`, and `prunes` (CLI:
`--type backup-incremental|restore|prune`), matching the direct `backup incremental`,
`restore`, and `prune` commands' options.

### Manage schedules

```bash
curl -X POST http://localhost:8000/cluster/1/schedules \
  -H "Authorization: Bearer $STARROCKS_BR_API_KEY" -H "Content-Type: application/json" \
  -d '{"job_type": "backup_full", "group_name": "production", "cadence": "0 1 * * 0"}'

# or via the CLI
starrocks-br api schedule add --cluster 1 --type backup_full --group production --cadence "0 1 * * 0"
starrocks-br api schedule list --cluster 1
```

To actually run due schedules, call `run-due` on a fixed interval (e.g. every minute) from cron
or a Kubernetes CronJob — see [Scheduling and Monitoring](scheduling.md):

```bash
starrocks-br api schedule run-due
```

## Next Steps

- [Scheduling and Monitoring](scheduling.md)
- [Core Concepts](core-concepts.md)
