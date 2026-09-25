# API Server

The FastAPI server lets you operate `starrocks-br` as a service: register multiple StarRocks
clusters, trigger and monitor backup/restore/prune jobs over HTTP, and manage recurring backup
schedules centrally. It's optional and fully additive — the existing direct-to-StarRocks CLI
commands (`backup`, `restore`, `prune`, `init`) work exactly as before and don't need it.

## Table of Contents

- [How It Fits Together](#how-it-fits-together)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Server](#running-the-server)
- [Authentication](#authentication)
- [Quickstart](#quickstart)
- [API Reference](#api-reference)
- [CLI Reference](#cli-reference)
- [Job Execution Backends](#job-execution-backends)
- [Scheduling](#scheduling)
- [Troubleshooting](#troubleshooting)

## How It Fits Together

```
+----------------------------------------------------------------------+
|  starrocks-br api serve   (FastAPI + Uvicorn, long-running process)  |
|                                                                        |
|   auth: bearer token (STARROCKS_BR_API_KEY)                          |
|   +----------------------------------------------------------+       |
|   | metadata store (SQLAlchemy; SQLite by default)            |      |
|   |   clusters, jobs, schedules                                |      |
|   +----------------------------------------------------------+       |
|   job execution backend: in-process thread pool (default)            |
+----------------------------------------------------------------------+
        |                          |                          |
        v                          v                          v
  StarRocks cluster A       StarRocks cluster B       StarRocks cluster C
  (registered via API)      (registered via API)      (registered via API)
```

Each registered cluster's own `ops` schema (`backup_history`, `table_inventory`, `run_status`,
`backup_partitions`) still lives inside that cluster, exactly as with the direct CLI — the API
server's own metadata store only tracks *which clusters exist* and *what jobs/schedules are
running against them*.

## Installation

The API server needs extra dependencies not required by the base CLI install:

```bash
pip install "starrocks-br[api]"
```

This adds FastAPI, Uvicorn, SQLAlchemy, Alembic, httpx, and croniter. See
[Installation Guide](installation.md#optional-api-server-support).

## Configuration

The server is configured entirely through environment variables (no server config YAML). It does
**not** read `config.yaml` — clusters are registered dynamically through the API/CLI instead.

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `STARROCKS_BR_API_KEY` | Yes | — | Shared bearer token every API request must present. The server refuses to start without it. |
| `STARROCKS_BR_DB_ENCRYPTION_KEY` | Yes | — | Key used to encrypt registered clusters' StarRocks passwords at rest. The server refuses to start without it. |
| `STARROCKS_BR_DATABASE_URL` | No | `sqlite:///./starrocks_br_api.db` | SQLAlchemy URL for the API's own metadata store. Point at MySQL/Postgres for production. |
| `STARROCKS_BR_ENABLED_BACKENDS` | No | `thread` | Comma-separated job execution backends to enable. Only `thread` ships today. |
| `STARROCKS_BR_DEFAULT_BACKEND` | No | `thread` | Backend used when a job/schedule doesn't specify one. |

Generate the two required secrets:

```bash
export STARROCKS_BR_API_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
export STARROCKS_BR_DB_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
```

Store these somewhere durable (secrets manager, `.env` file kept out of git, Kubernetes Secret).
Losing `STARROCKS_BR_DB_ENCRYPTION_KEY` makes already-stored cluster passwords unrecoverable —
you'd need to re-`PATCH` each cluster with its password again.

### Metadata store migrations

The metadata store's schema is managed by Alembic. On first run against a fresh `DATABASE_URL`:

```bash
alembic upgrade head
```

Run this from the repository root (it uses `alembic.ini`, which reads `STARROCKS_BR_DATABASE_URL`
the same way the server does). The default SQLite file is created automatically if it doesn't
exist yet — you only need to run migrations explicitly when pointing at MySQL/Postgres, or to
apply a schema update after upgrading `starrocks-br`.

## Running the Server

```bash
starrocks-br api serve --host 0.0.0.0 --port 8000
```

Equivalent direct `uvicorn` invocation (useful for `--workers`, reload during development, etc. —
note the API is stateful in-process for the `thread` backend, so running multiple workers means
each worker has its own job queue and only sees jobs it itself submitted):

```bash
uvicorn starrocks_br.api.app:create_app --factory --host 0.0.0.0 --port 8000
```

Check it's up:

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

`/health` is the only endpoint that doesn't require authentication.

## Authentication

Every other endpoint requires an `Authorization: Bearer <token>` header matching
`STARROCKS_BR_API_KEY`:

```bash
curl -H "Authorization: Bearer $STARROCKS_BR_API_KEY" http://localhost:8000/clusters
```

There are no user accounts or sessions — one shared secret protects the whole server. Requests
without a token, or with the wrong one, get `401 Unauthorized`.

## Quickstart

```bash
# 1. Install and configure
pip install "starrocks-br[api]"
export STARROCKS_BR_API_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
export STARROCKS_BR_DB_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

# 2. Start the server (in another terminal, or as a background service)
starrocks-br api serve

# 3. Point the CLI's api-client commands at it
export STARROCKS_BR_API_URL=http://localhost:8000
export STARROCKS_BR_API_KEY=<same key as above>

# 4. Register a cluster
starrocks-br api cluster add \
  --name prod-eu --host sr.internal --port 9030 --user root --password secret \
  --database mydb --repository s3_repo

# 5. Initialize its ops schema (still a direct CLI operation against that cluster)
cat > prod-eu.yaml <<EOF
host: sr.internal
port: 9030
user: root
database: mydb
repository: s3_repo
table_inventory:
  - group: production
    tables:
      - {database: mydb, table: "*"}
EOF
STARROCKS_PASSWORD=secret starrocks-br init --config prod-eu.yaml

# 6. Run a backup through the API and wait for it to finish
starrocks-br api job submit --cluster 1 --type backup-full --group production --wait
```

## API Reference

All request/response bodies are JSON. `{id}` path segments are integers.

### Health

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | No | Liveness check. Returns `{"status": "ok"}`. |

### Clusters

| Method | Path | Description |
|--------|------|-------------|
| POST | `/clusters` | Register a new cluster. |
| GET | `/clusters` | List registered clusters. |
| GET | `/clusters/{id}` | Get one cluster. |
| PATCH | `/clusters/{id}` | Update connection fields (partial). |
| DELETE | `/clusters/{id}` | Remove a cluster (blocked if it has a PENDING/RUNNING job or an enabled schedule). |

`POST /clusters` body:

```json
{
  "name": "prod-eu",
  "host": "sr.internal",
  "port": 9030,
  "user": "root",
  "password": "secret",
  "database": "mydb",
  "repository": "s3_repo",
  "ops_database": "ops",
  "default_backend": "thread"
}
```
`password` may be an empty string (StarRocks permits passwordless users). `ops_database` and
`default_backend` are optional and default to `"ops"`/`"thread"`. Responses never include the
password in any form.

### Jobs

| Method | Path | Description |
|--------|------|-------------|
| POST | `/clusters/{id}/backups/full` | Submit a full backup. |
| POST | `/clusters/{id}/backups/incremental` | Submit an incremental backup. |
| POST | `/clusters/{id}/restores` | Submit a restore. |
| POST | `/clusters/{id}/prunes` | Submit a prune. |
| GET | `/jobs/{id}` | Get a job's status/progress. |

Every submit endpoint returns `202 Accepted` immediately with the created job (`status: PENDING`);
the work runs asynchronously. Request body fields (all optional, send only what applies):

| Field | Used by | Meaning |
|-------|---------|---------|
| `group` | backups, restores | Inventory group name. |
| `name` | backups | Custom backup label. |
| `baseline_backup` | incremental backup | Baseline backup label to diff against. |
| `target_label` | restore | Backup label to restore. |
| `table` | restore | Restore a single table instead of a group. |
| `rename_suffix` | restore | Temp-table suffix (default `_restored`). |
| `keep_last`, `older_than`, `snapshot`, `snapshots`, `dry_run` | prune | Same semantics as the CLI's `prune` options — exactly one of `keep_last`/`older_than`/`snapshot`/`snapshots` is required. |
| `backend` | all | Override the execution backend for this one job. |

`GET /jobs/{id}` response:

```json
{
  "id": 42,
  "cluster_id": 1,
  "job_type": "backup_full",
  "backend": "thread",
  "status": "RUNNING",
  "progress_pct": 55,
  "state_detail": "UPLOADING",
  "error_message": null,
  "created_at": "2026-01-01T01:00:00Z",
  "started_at": "2026-01-01T01:00:01Z",
  "finished_at": null
}
```

`status` is one of `PENDING`, `RUNNING`, `SUCCESS`, `FAILED`. `progress_pct` is a 0–100 integer
when StarRocks currently reports one for that phase (mainly during upload/download), and `null`
otherwise — `state_detail` (e.g. `SNAPSHOTING`, `UPLOADING`, `COMMITTING`, `FINISHED`) is set
either way, so you always know a job is progressing even without a percentage. `error_message` is
set only when `status` is `FAILED`.

### Schedules

| Method | Path | Description |
|--------|------|-------------|
| POST | `/schedules` | Create a recurring schedule. |
| GET | `/schedules` | List schedules. |
| GET | `/schedules/{id}` | Get one schedule. |
| PATCH | `/schedules/{id}` | Update cadence/group/backend/enabled (partial). |
| DELETE | `/schedules/{id}` | Delete a schedule. |
| POST | `/schedules/run-due` | Trigger every enabled schedule that's currently due. |

`POST /schedules` body:

```json
{
  "cluster_id": 1,
  "job_type": "backup_full",
  "group_name": "production",
  "cadence": "0 1 * * 0",
  "backend": null,
  "enabled": true
}
```
`job_type` is `backup_full` or `backup_incremental`. `cadence` is a standard cron expression.

`POST /schedules/run-due` response:

```json
{"triggered_job_ids": [43, 44], "triggered_count": 2}
```
Safe to call repeatedly and concurrently — each due occurrence is only ever triggered once.

### Repositories

Repository operations are a live pass-through to the target cluster — nothing is cached locally,
and submitted S3 credentials are never stored in the API's own metadata store.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/clusters/{id}/repositories` | List repositories that currently exist on this cluster. |
| POST | `/clusters/{id}/repositories` | Create a new S3-compatible repository on this cluster. |
| DELETE | `/clusters/{id}/repositories/{name}` | Delete a repository from this cluster (blocked if it holds any snapshot). |

`GET /clusters/{id}/repositories` response:

```json
[
  {
    "name": "s3_repo",
    "location": "s3://backups/starrocks",
    "broker": "",
    "is_read_only": false,
    "error": null
  }
]
```
Returns `503` (rather than an empty list) if the cluster can't currently be reached.

`POST /clusters/{id}/repositories` body:

```json
{
  "name": "s3_repo",
  "location": "s3://backups/starrocks",
  "access_key": "your-access-key",
  "secret_key": "your-secret-key",
  "endpoint": "https://s3.amazonaws.com",
  "region": "us-west-2"
}
```
`region` is optional. Returns `409` if a repository with that name already exists on the cluster.
The response body is a `RepositoryRead` object (as above); the submitted `access_key`/`secret_key`
are forwarded to StarRocks and never written to the API's own metadata store.

`DELETE /clusters/{id}/repositories/{name}` checks StarRocks' own `SHOW SNAPSHOT ON <repo>` first
and returns `409` if the repository still holds any snapshot, `404` if the cluster or repository
doesn't exist, or `204` on successful deletion.

## CLI Reference

`starrocks-br api ...` commands are thin HTTP clients for the endpoints above. They never talk to
StarRocks directly. Configure the target server once via environment variables, or pass
`--api-url`/`--api-key` on each command:

```bash
export STARROCKS_BR_API_URL=http://localhost:8000
export STARROCKS_BR_API_KEY=<your key>
```

| Command | Equivalent endpoint |
|---------|---------------------|
| `starrocks-br api serve [--host] [--port]` | starts the server |
| `starrocks-br api cluster add --name ... --host ... --port ... --user ... --password ... --database ... --repository ...` | `POST /clusters` |
| `starrocks-br api cluster list` | `GET /clusters` |
| `starrocks-br api cluster remove <id>` | `DELETE /clusters/{id}` |
| `starrocks-br api job submit --cluster <id> --type backup-full\|backup-incremental\|restore\|prune [options] [--wait]` | `POST /clusters/{id}/...` |
| `starrocks-br api job status <id>` | `GET /jobs/{id}` |
| `starrocks-br api schedule add --cluster <id> --type backup_full\|backup_incremental --group ... --cadence ...` | `POST /schedules` |
| `starrocks-br api schedule list` | `GET /schedules` |
| `starrocks-br api schedule remove <id>` | `DELETE /schedules/{id}` |
| `starrocks-br api schedule run-due` | `POST /schedules/run-due` |
| `starrocks-br api repository add --cluster <id> --name ... --location ... --access-key ... --secret-key ... --endpoint ... [--region ...]` | `POST /clusters/{id}/repositories` |
| `starrocks-br api repository list --cluster <id>` | `GET /clusters/{id}/repositories` |
| `starrocks-br api repository remove --cluster <id> <name>` | `DELETE /clusters/{id}/repositories/{name}` |

`job submit --wait` polls `GET /jobs/{id}` until the job reaches `SUCCESS` or `FAILED`, printing
progress as it goes, and exits non-zero on failure — useful in scripts/CI.

Run `starrocks-br api --help`, or `starrocks-br api <group> --help`, for the full option list.

## Job Execution Backends

Jobs run on a pluggable backend, chosen per request (`backend` field / `--backend` flag), falling
back to the cluster's `default_backend`, falling back to the server's `STARROCKS_BR_DEFAULT_BACKEND`.
Only `thread` (an in-process thread pool inside the API server) ships today — it needs no extra
infrastructure. The backend is an abstraction point for future work (e.g. a Kafka- or Redis-backed
backend for distributed workers); adding one doesn't change this API's request/response contracts.

## Scheduling

See [Scheduling and Monitoring](scheduling.md#recommended-api-managed-schedules) for how to wire
`starrocks-br api schedule run-due` into cron or a Kubernetes CronJob so due schedules actually
get triggered.

## Troubleshooting

**Server won't start / exits immediately with an error about `STARROCKS_BR_API_KEY` or
`STARROCKS_BR_DB_ENCRYPTION_KEY`.** Both must be set before `starrocks-br api serve` (or
`uvicorn ...`) is invoked — see [Configuration](#configuration).

**`401` from every request.** Check `Authorization: Bearer <token>` is present and matches the
server's `STARROCKS_BR_API_KEY` exactly (the CLI reads `STARROCKS_BR_API_KEY`/`--api-key` too —
make sure it's set in the *client's* environment, not just the server's).

**`422` on cluster/schedule creation.** The response body's `detail` lists which field failed
validation (e.g. a missing required field, a bad cron expression, or an unknown `backend` name not
in `STARROCKS_BR_ENABLED_BACKENDS`).

**`409` deleting a cluster.** It has a job in `PENDING`/`RUNNING` state, or an enabled schedule.
Wait for the job to finish (or investigate it) and/or disable or delete the schedule first.

**`409` deleting a repository.** StarRocks reports it still holds at least one snapshot
(`SHOW SNAPSHOT ON <repo>`) — prune or restore-and-confirm the backup data first, or leave the
repository in place. Note this checks StarRocks' live state, not this tool's own backup history.

**`503` on repository endpoints.** The target cluster couldn't be reached (as opposed to it having
zero repositories, which is a normal `200`/`[]` response) — check connectivity/credentials for that
cluster.

**A job stays `RUNNING` far longer than expected.** Poll `GET /jobs/{id}` for `state_detail` —
it mirrors StarRocks' own `SHOW BACKUP`/`SHOW RESTORE` state (`SNAPSHOTING`, `UPLOADING`,
`DOWNLOADING`, `COMMITTING`, ...). If it's stuck, check that state directly against StarRocks
(`SHOW BACKUP FROM <db>` / `SHOW RESTORE FROM <db>`) and its BE logs — the API surfaces StarRocks'
own state faithfully, it doesn't second-guess it.

**Lost cluster passwords after rotating `STARROCKS_BR_DB_ENCRYPTION_KEY`.** This is expected —
the key encrypts passwords at rest. Re-`PATCH` affected clusters with their password.
