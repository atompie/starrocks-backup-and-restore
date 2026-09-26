# StarRocks Backup & Restore

Full and incremental backup automation for StarRocks shared-nothing clusters.

**Requirements:** StarRocks 3.5+ (shared-nothing mode)

📋 **[Release Notes & Changelog](CHANGELOG.md)**

## Documentation

- [Why This Tool?](#why-this-tool) (this page)
- [Installation](#installation) (this page)
- [Basic Usage](#basic-usage) (this page)
- [How It Works](#how-it-works) (this page)
- **[Getting Started](docs/getting-started.md)** - Step-by-step tutorial
- **[Core Concepts](docs/core-concepts.md)** - Understand inventory groups, backup types, and restore chains
- **[Installation Guide](docs/installation.md)** - All installation methods
- **[Configuration Reference](docs/configuration.md)** - Server configuration and TLS setup
- **[API Server](docs/api.md)** - Multi-cluster registry, jobs, and schedules over HTTP
- **[Scheduling & Monitoring](docs/scheduling.md)** - Automate backups and monitor status

## Why This Tool?

StarRocks provides native `BACKUP` and `RESTORE` commands, but they only support full backups. For large-scale deployments hosting data at petabyte scale, full backups are not feasible due to time, storage, and network constraints.

This tool adds **incremental backup capabilities** to StarRocks by leveraging native partition-based backup features.

**What StarRocks doesn't provide:**
- ❌ **No incremental backups** - You must manually identify changed partitions and build complex backup commands
- ❌ **No backup history** - No built-in way to track what was backed up, when, or which backups succeeded/failed
- ❌ **No restore intelligence** - You manually determine which backups are needed for point-in-time recovery
- ❌ **No organization** - No way to group tables or manage different backup strategies
- ❌ **No concurrency control** - Multiple backup operations can conflict

**What this tool provides:**
- ✅ **Automatic incremental backups** - Tool detects changed partitions since the last full backup automatically
- ✅ **Complete operation tracking** - Every backup and restore is logged with status, timestamps, and error details
- ✅ **Intelligent restore** - Automatically resolves backup chains (full + incremental) for you
- ✅ **Inventory groups** - Organize tables into groups with different backup strategies
- ✅ **Backup lifecycle management** - Prune old backups with flexible retention policies (keep-last, older-than, specific snapshots)
- ✅ **Job concurrency control** - Prevents conflicting operations
- ✅ **Safe restores** - Atomic rename mechanism prevents data loss during restore
- ✅ **Metadata management** - Dedicated `ops` database tracks all backup metadata and partition manifests

In short: this tool transforms StarRocks's basic backup/restore commands into a **production-ready incremental backup solution**.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install starrocks-br
```

See [Installation Guide](docs/installation.md) for all options.

## Basic Usage

**Configure and start the server:**
```bash
export STARROCKS_BR_API_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
export STARROCKS_BR_DB_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
uvicorn starrocks_br.api.app:create_app --factory
```

**Register a cluster:**
```bash
curl -s -H "Authorization: Bearer $STARROCKS_BR_API_KEY" -H "Content-Type: application/json" \
  -X POST http://localhost:8000/cluster \
  -d '{"name": "prod", "host": "127.0.0.1", "port": 9030, "user": "root",
       "password": "your_password", "database": "your_database", "repository": "your_repo_name"}'
```

**Create an inventory group:**
```bash
curl -s -H "Authorization: Bearer $STARROCKS_BR_API_KEY" -H "Content-Type: application/json" \
  -X POST http://localhost:8000/inventories/cluster/1 \
  -d '{"name": "production", "tables": [{"database": "mydb", "table": "users"}, {"database": "mydb", "table": "orders"}]}'
```

**Backup:**
```bash
# Full backup
curl -s -H "Authorization: Bearer $STARROCKS_BR_API_KEY" -H "Content-Type: application/json" \
  -X POST http://localhost:8000/backup/manual/full/cluster/1 \
  -d '{"group_id": 1, "repository": "your_repo_name"}'

# Incremental backup (tool detects changed partitions automatically)
curl -s -H "Authorization: Bearer $STARROCKS_BR_API_KEY" -H "Content-Type: application/json" \
  -X POST http://localhost:8000/backup/manual/incremental/cluster/1 \
  -d '{"group_id": 1, "repository": "your_repo_name"}'
```

**Restore:**
```bash
# Tool automatically resolves backup chains
curl -s -H "Authorization: Bearer $STARROCKS_BR_API_KEY" -H "Content-Type: application/json" \
  -X POST http://localhost:8000/backup/manual/restore/cluster/1 \
  -d '{"target_label": "mydb_20251118_full"}'
```

**Prune old backups:**
```bash
# Keep only last 5 backups
curl -s -H "Authorization: Bearer $STARROCKS_BR_API_KEY" -H "Content-Type: application/json" \
  -X POST http://localhost:8000/backup/manual/prune/cluster/1 \
  -d '{"group_id": 1, "keep_last": 5}'

# Delete backups older than a date
curl -s -H "Authorization: Bearer $STARROCKS_BR_API_KEY" -H "Content-Type: application/json" \
  -X POST http://localhost:8000/backup/manual/prune/cluster/1 \
  -d '{"group_id": 1, "older_than": "2024-01-01 00:00:00"}'
```

See [API Server](docs/api.md) for the full reference and [Configuration Reference](docs/configuration.md) for TLS and advanced options.

## How It Works

1. **Inventory Groups**: Define collections of tables that share the same backup strategy
2. **ops Database**: Tool creates an `ops` database to track all operations and metadata
3. **Automatic Incrementals**: Tool queries partition metadata and compares with the baseline to detect changes
4. **Intelligent Restore**: Automatically resolves backup chains (full + incremental) for point-in-time recovery
5. **Safe Operations**: All restores use temporary tables with atomic rename for safety

Read [Core Concepts](docs/core-concepts.md) for detailed explanations.

## Contributing

We welcome contributions! See issues for areas that need help or create a new issue to report a bug or request a feature.

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.