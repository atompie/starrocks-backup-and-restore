# Configuration Reference

## Server Configuration

The server is configured entirely through environment variables — there is no server config file.
StarRocks clusters, inventory groups, and schedules are all registered dynamically through the
API. See [API Server](api.md) for the full guide.

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `STARROCKS_BR_API_KEY` | Yes | — | Shared bearer token every API request must present. Server refuses to start without it. Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `STARROCKS_BR_DB_ENCRYPTION_KEY` | Yes | — | Key used to encrypt registered clusters' StarRocks passwords at rest. Server refuses to start without it. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. |
| `STARROCKS_BR_DATABASE_URL` | No | `sqlite:///./starrocks_br_api.db` | SQLAlchemy URL for the API's own metadata store (registered clusters, jobs, schedules, table inventory, backup/restore history). Point this at MySQL/Postgres for production; run `alembic upgrade head` (from the repo root, using `alembic.ini`) against it first. |
| `STARROCKS_BR_ENABLED_BACKENDS` | No | `thread` | Comma-separated list of job execution backends to enable. Only `thread` (in-process) ships today. |
| `STARROCKS_BR_DEFAULT_BACKEND` | No | `thread` | Backend used when a job submission or schedule doesn't specify one. |

Store the two required secrets somewhere durable (secrets manager, `.env` file kept out of git,
Kubernetes Secret). Losing `STARROCKS_BR_DB_ENCRYPTION_KEY` makes already-stored cluster passwords
unrecoverable — you'd need to re-`PATCH` each cluster with its password again.

## Registering a Cluster

Clusters, their connection details, and their backup repository are registered through the API,
not through a config file:

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
    "repository": "your_repo_name"
  }'
```

`name` is an optional stable identity for this cluster in the metadata store; if omitted, one is
derived from `host`/`port`/`database`. `password` may be an empty string (StarRocks permits
passwordless users) and is encrypted at rest with `STARROCKS_BR_DB_ENCRYPTION_KEY`. See
[API Server: Clusters](api.md#clusters) for the full field reference, and
[Core Concepts](core-concepts.md) for how inventory groups scope which tables get backed up.

**Note:** TLS-encrypted connections to StarRocks are not currently exposed through the cluster
registration API — this is a known gap, not a supported option today.

## Repository Setup

Create a backup repository in StarRocks before using the tool.

> The API can also create, list, and delete S3-compatible repositories on a registered cluster
> instead of hand-writing the SQL below — see [API Reference: Repositories](api.md#repositories).
> The manual SQL below is still the only option for HDFS/Azure repositories.

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

Then reference it by name when registering a cluster or submitting a backup.

## Next Steps

- [Getting Started](getting-started.md)
- [API Server](api.md)
