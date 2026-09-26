# Getting Started

This guide walks you through setting up the API server and running your first backup with
StarRocks Backup & Restore.

## Prerequisites

Before you begin, ensure you have:

1. **StarRocks 3.5 or later** running in **shared-nothing mode**
   - Shared-data architecture is not currently supported
   - Earlier versions (< 3.5) are not supported due to differences in `SHOW FRONTENDS` and `SHOW BACKENDS` output formats
2. **A backup repository** - You need to create this in StarRocks first (see [Repository Setup](#repository-setup) below)
3. **Database access** - User account with backup/restore privileges
4. **Python 3.10+**

## Repository Setup

First, create a backup repository in StarRocks. This defines where your backup data will be stored.

Connect to your StarRocks cluster and run:

```sql
CREATE REPOSITORY `my_backup_repo`
WITH BROKER
ON LOCATION "s3://your-backup-bucket/backups/"
PROPERTIES (
    "aws.s3.access_key" = "your-access-key",
    "aws.s3.secret_key" = "your-secret-key",
    "aws.s3.endpoint" = "https://s3.amazonaws.com"
);
```

Verify it was created:

```sql
SHOW REPOSITORIES;
```

For other storage backends (HDFS, Azure Blob, etc.), see the [StarRocks documentation](https://docs.starrocks.io/docs/administration/management/Backup_and_restore/).

## Installation

```bash
# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Linux/Mac
# .venv\Scripts\activate    # On Windows

# Install the package
pip install starrocks-br
```

**Note:** Always activate the virtual environment before using the tool.

See [Installation Guide](installation.md) for more installation options.

## Start the API Server

The server needs two secrets: an API key that clients authenticate with, and an encryption key
used to store registered clusters' passwords at rest.

```bash
export STARROCKS_BR_API_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
export STARROCKS_BR_DB_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

uvicorn starrocks_br.api.app:create_app --factory
```

Check it's up:

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

See [API Server](api.md) and [Configuration Reference](configuration.md) for the full set of
server environment variables and production options (e.g. pointing the metadata store at
MySQL/Postgres instead of the default SQLite file).

## Register Your Cluster

Register the StarRocks cluster you want to back up:

```bash
curl -s -H "Authorization: Bearer $STARROCKS_BR_API_KEY" -H "Content-Type: application/json" \
  -X POST http://localhost:8000/cluster \
  -d '{
    "name": "prod",
    "host": "127.0.0.1",
    "port": 9030,
    "user": "root",
    "password": "your_password",
    "database": "your_database",
    "repository": "my_backup_repo"
  }'
# -> {"id": 1, "name": "prod", ...}
```

Note the returned `id` — every other call below is scoped to this cluster.

## Define Your Backup Groups

Inventory groups are named sets of database/table memberships that scope backup, restore, and
prune operations. Create one for the tables you want to back up:

```bash
curl -s -H "Authorization: Bearer $STARROCKS_BR_API_KEY" -H "Content-Type: application/json" \
  -X POST http://localhost:8000/inventories/cluster/1 \
  -d '{
    "name": "important_tables",
    "tables": [
      {"database": "your_database", "table": "users"},
      {"database": "your_database", "table": "orders"}
    ]
  }'
# -> {"id": 1, "name": "important_tables"}
```

Use a `"*"` table name to back up every table in a database:

```bash
curl -s -H "Authorization: Bearer $STARROCKS_BR_API_KEY" -H "Content-Type: application/json" \
  -X POST http://localhost:8000/inventories/cluster/1 \
  -d '{"name": "full_backup", "tables": [{"database": "your_database", "table": "*"}]}'
```

Add or remove individual table memberships later with `POST`/`DELETE`
`/inventory/cluster/{cluster_id}/group_id/{group_id}/tables/...` — see [API Server](api.md#inventory-groups).

Not sure how to group your tables? See [Core Concepts: Inventory Groups](core-concepts.md#inventory-groups) for guidance.

## Run Your First Backup

Now you're ready to run a backup! Submitting a job returns immediately with a job id; the backup
runs asynchronously.

### Full Backup

```bash
curl -s -H "Authorization: Bearer $STARROCKS_BR_API_KEY" -H "Content-Type: application/json" \
  -X POST http://localhost:8000/backup/manual/full/cluster/1 \
  -d '{"group_id": 1, "repository": "my_backup_repo"}'
# -> 202 {"id": 1, "cluster_id": 1, "job_type": "backup_full", "status": "PENDING", ...}
```

### Monitor the Backup

Poll the job until it reaches a terminal state:

```bash
curl -s -H "Authorization: Bearer $STARROCKS_BR_API_KEY" http://localhost:8000/job/1
# -> {"status": "RUNNING", "progress_pct": 55, "state_detail": "UPLOADING", ...}
# -> {"status": "SUCCESS", "started_at": "...", "finished_at": "...", ...}
```

`status` is one of `PENDING`, `RUNNING`, `SUCCESS`, `FAILED`. The tool will:
1. Verify cluster health
2. Find all tables in the `important_tables` group
3. Execute the backup
4. Track progress until completion
5. Record the result in its own metadata store (`GET /job/{id}`)

## Run an Incremental Backup

After you have a full backup, run incremental backups to capture only changed partitions:

```bash
curl -s -H "Authorization: Bearer $STARROCKS_BR_API_KEY" -H "Content-Type: application/json" \
  -X POST http://localhost:8000/backup/manual/incremental/cluster/1 \
  -d '{"group_id": 1, "repository": "my_backup_repo"}'
```

The tool automatically:
1. Finds the most recent full backup for the group
2. Compares current partitions with the baseline
3. Backs up only new or modified partitions

**Note:** Incremental backups require partitioned tables. If your tables aren't partitioned, use full backups.

## Restore from a Backup

To restore data from a backup, you need its label, found on the completed job (`GET /job/{id}`
records the backup label in its own history — see [API Server](api.md) for how to look up
backup history for a cluster).

### Restore All Tables

```bash
curl -s -H "Authorization: Bearer $STARROCKS_BR_API_KEY" -H "Content-Type: application/json" \
  -X POST http://localhost:8000/backup/manual/restore/cluster/1 \
  -d '{"target_label": "your_backup_label_here"}'
```

### Restore Specific Group

```bash
curl -s -H "Authorization: Bearer $STARROCKS_BR_API_KEY" -H "Content-Type: application/json" \
  -X POST http://localhost:8000/backup/manual/restore/cluster/1 \
  -d '{"target_label": "your_backup_label_here", "group_id": 1}'
```

### Restore Single Table

```bash
curl -s -H "Authorization: Bearer $STARROCKS_BR_API_KEY" -H "Content-Type: application/json" \
  -X POST http://localhost:8000/backup/manual/restore/cluster/1 \
  -d '{"target_label": "your_backup_label_here", "table": "users", "database": "your_database"}'
```

The tool automatically handles backup chains - if you specify an incremental backup, it will restore the base full backup first, then apply the incremental.

## Verify the Restore

The restore process uses temporary tables with a `_restored` suffix. After restore completes, you can verify the data before it's made live:

```sql
-- Check the restored data
SELECT COUNT(*) FROM users_restored;
SELECT * FROM users_restored LIMIT 10;

-- Compare with current data if needed
SELECT COUNT(*) FROM users;
```

The tool performs atomic renames to swap the temp tables with the live tables only after the restore succeeds.

## Next Steps

Now that you've completed your first backup and restore:

- **Understand the concepts**: Read [Core Concepts](core-concepts.md) to deepen your understanding
- **Explore the full API**: See [API Server](api.md) for every endpoint and field
- **Automate backups**: Learn about scheduling in [Scheduling and Monitoring](scheduling.md)
- **Advanced configuration**: Check [Configuration Reference](configuration.md) for TLS and advanced settings

## Troubleshooting

**`404` submitting a job with a bad `group_id` or `repository`**
- Confirm the group exists: `GET /inventories/cluster/{cluster_id}`
- Confirm the repository exists on the cluster: `GET /repositories/cluster/{cluster_id}`

**`404` from every request against a cluster**
- Verify the cluster id is correct: `GET /clusters`

**"No full backup found for incremental"**
- Run a full backup for that group first

**`401` from every request**
- Check `Authorization: Bearer <token>` is present and matches the server's `STARROCKS_BR_API_KEY`

**Server won't start**
- Ensure `STARROCKS_BR_API_KEY` and `STARROCKS_BR_DB_ENCRYPTION_KEY` are both set

For more help, see the [GitHub Issues](https://github.com/deep-bi/starrocks-backup-and-restore/issues).
