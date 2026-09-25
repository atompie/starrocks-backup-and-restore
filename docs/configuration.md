# Configuration Reference

## Basic Configuration

Create a `config.yaml` file:

```yaml
host: "127.0.0.1"
port: 9030
user: "root"
database: "your_database"
repository: "your_repo_name"
```

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `host` | string | Yes | StarRocks FE host address |
| `port` | integer | Yes | StarRocks MySQL protocol port (default: 9030) |
| `user` | string | Yes | Database user with backup/restore privileges |
| `database` | string | Yes | Database containing tables to backup |
| `repository` | string | Yes | Repository name (created via `CREATE REPOSITORY`) |
| `name` | string | No | Optional stable identity for this cluster in the local metastore (see below). If omitted, one is derived from `host`/`port`/`database`. |
| `table_inventory` | list | No | Table inventory groups definition (see below) |

**Note:** The `database` field specifies which database contains your tables to back up.

**Breaking change:** `ops_database` has been removed. Backup/restore bookkeeping
(table inventory, backup/restore history, job-concurrency locks, backup
partition manifests) no longer lives in a StarRocks-side database at all - it
lives in this tool's own SQLite (or MySQL/Postgres, via `STARROCKS_BR_DATABASE_URL`)
metastore, the same one the API server uses, scoped per cluster. This means:

- `alembic upgrade head` (from the repo root, against `STARROCKS_BR_DATABASE_URL`)
  and `STARROCKS_BR_DB_ENCRYPTION_KEY` (see the [API Server guide](api.md)) are
  now required before running `starrocks-br init` or any other CLI command -
  previously the CLI needed nothing beyond a YAML file.
- `starrocks-br init --config config.yaml` registers this config's cluster in
  that metastore (get-or-create, keyed by the identity above) and bootstraps
  `table_inventory` from the YAML's `table_inventory` section, if present.
  Every other command (`backup incremental`, `backup full`, `restore`,
  `prune`) requires `init` to have been run first for this config, and fails
  clearly if not - it will not silently register a cluster on a typo'd
  `--config` path.
- Re-running `init` with the same config refreshes the stored connection
  fields (host/port/user/password/database/repository) from the YAML, so the
  config file stays the source of truth.

## Table Inventory Configuration

Define table inventory groups directly in your config file to avoid manual SQL inserts.

```yaml
host: "127.0.0.1"
port: 9030
user: "root"
database: "quickstart"
repository: "minio_repo"

table_inventory:
  - group: "full_backup"
    tables:
      - database: "production_db"
        table: "*"  # Wildcard for all tables

  - group: "fact_tables"
    tables:
      - database: "production_db"
        table: "fact_sales"
      - database: "production_db"
        table: "fact_orders"
```

### Table Inventory YAML Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `group` | string | Yes | Inventory group name |
| `tables` | list | Yes | List of table definitions |
| `tables[].database` | string | Yes | Database name |
| `tables[].table` | string | Yes | Table name or `"*"` for all tables |

When you run `starrocks-br init`, table inventory is automatically populated from your config. Manual SQL INSERT statements still work if you prefer that approach.

**Note:** If you modify the `table_inventory` section in your config file after the initial setup, you must rerun `starrocks-br init --config <config_file>` to update the database with your changes. The table inventory is persisted in the `ops.table_inventory` table and is not automatically updated when you change the config file.

## Password Management

Never store passwords in config files. Use an environment variable:

**Linux/macOS:**
```bash
export STARROCKS_PASSWORD="your_password"
```

**Windows (PowerShell):**
```powershell
$env:STARROCKS_PASSWORD="your_password"
```

**Windows (Command Prompt):**
```cmd
set STARROCKS_PASSWORD=your_password
```

## TLS/SSL Configuration

Add a `tls` section to enable encrypted connections.

### Server Authentication

```yaml
host: "127.0.0.1"
port: 9030
user: "root"
database: "your_database"
repository: "your_repo_name"

tls:
  enabled: true
  ca_cert: "/path/to/ca.pem"
```

### Mutual TLS (mTLS)

```yaml
tls:
  enabled: true
  ca_cert: "/path/to/ca.pem"
  client_cert: "/path/to/client-cert.pem"
  client_key: "/path/to/client-key.pem"
```

### TLS Options

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `enabled` | boolean | Yes | false | Enable TLS |
| `ca_cert` | string | Yes* | - | CA certificate path |
| `client_cert` | string | No | - | Client certificate (for mTLS) |
| `client_key` | string | No | - | Client private key (for mTLS) |
| `verify_server_cert` | boolean | No | true | Verify server certificate |
| `tls_versions` | list | No | ["TLSv1.2", "TLSv1.3"] | Allowed TLS versions |

*Required when `enabled: true`

## Repository Setup

Create a backup repository in StarRocks before using the tool.

> If you're running the [API server](api.md), you can also create, list, and delete
> S3-compatible repositories on a registered cluster through the API/CLI instead of hand-writing
> the SQL below — see [API Reference: Repositories](api.md#repositories). The manual SQL below is
> still the only option for HDFS/Azure repositories, or if you're not running the API server.

### S3-Compatible Storage

```sql
CREATE REPOSITORY `s3_backup_repo`
WITH BROKER
ON LOCATION "s3://your-backup-bucket/backups/"
PROPERTIES (
    "aws.s3.access_key" = "your-access-key",
    "aws.s3.secret_key" = "your-secret-key",
    "aws.s3.endpoint" = "https://s3.amazonaws.com",
    "aws.s3.region" = "us-west-2",
    "aws.s3.enable_path_style_access" = "true"
);
```
`WITH BROKER` is StarRocks' only repository clause (there is no separate `WITH S3` clause) —
S3-compatible storage is selected by the `aws.s3.*` properties. `aws.s3.enable_path_style_access`
is needed for non-AWS S3-compatible stores (e.g. MinIO, RustFS); omit it for real AWS S3 if you
prefer virtual-hosted-style addressing.

### HDFS Storage

```sql
CREATE REPOSITORY `hdfs_backup_repo`
WITH BROKER
ON LOCATION "hdfs://namenode:9000/backups/"
PROPERTIES (
    "username" = "hdfs",
    "password" = ""
);
```

### Azure Blob Storage

```sql
CREATE REPOSITORY `azure_backup_repo`
WITH BROKER
ON LOCATION "wasb://container@account.blob.core.windows.net/backups/"
PROPERTIES (
    "azure.blob.storage_account" = "your-account",
    "azure.blob.shared_key" = "your-key"
);
```

### Verify Repository

```sql
SHOW REPOSITORIES;
```

Then reference it in your config:

```yaml
repository: "s3_backup_repo"
```

## API Server Configuration

The optional FastAPI server (`pip install "starrocks-br[api]"`, then `starrocks-br api serve`) is
configured entirely through environment variables — there is no server config YAML file. It does
not read `config.yaml`; StarRocks clusters are registered dynamically through the API/CLI instead.
See the **[API Server guide](api.md)** for the full reference.

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `STARROCKS_BR_API_KEY` | Yes | — | Shared bearer token every API request must present. Server refuses to start without it. Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `STARROCKS_BR_DB_ENCRYPTION_KEY` | Yes | — | Key used to encrypt registered clusters' StarRocks passwords at rest. Server refuses to start without it. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. |
| `STARROCKS_BR_DATABASE_URL` | No | `sqlite:///./starrocks_br_api.db` | SQLAlchemy URL for the API's own metadata store (registered clusters, jobs, schedules). Point this at MySQL/Postgres for production; run `alembic upgrade head` (from the repo root, using `alembic.ini`) against it first. |
| `STARROCKS_BR_ENABLED_BACKENDS` | No | `thread` | Comma-separated list of job execution backends to enable. Only `thread` (in-process) ships today. |
| `STARROCKS_BR_DEFAULT_BACKEND` | No | `thread` | Backend used when a job submission or schedule doesn't specify one. |

The CLI's own API-client commands (`starrocks-br api ...`) read two more variables so they know
which server to talk to:

| Variable | Purpose |
|----------|---------|
| `STARROCKS_BR_API_URL` | Base URL of the running API server. Can also be passed per-command with `--api-url`. |
| `STARROCKS_BR_API_KEY` | Same bearer token configured on the server. Can also be passed per-command with `--api-key`. |

This is unrelated to `STARROCKS_PASSWORD`, which remains how the *direct* (non-API) CLI commands
authenticate to a StarRocks cluster from `config.yaml`.

## Next Steps

- [Getting Started](getting-started.md)
- [Command Reference](commands.md)
