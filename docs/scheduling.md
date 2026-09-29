# Scheduling and Monitoring

Guide for automating backups and monitoring their status.

## Scheduling Backups

### Recommended: API-managed schedules

Define schedules through the API instead of hand-writing per-group cron entries — schedules are
centrally visible and manageable (`GET /backup/schedules/cluster/{id}`), and cadence/group/backend
changes take effect without editing crontab files. Register each schedule once:

```bash
curl -s -H "Authorization: Bearer $STARROCKS_BR_API_KEY" -H "Content-Type: application/json" \
  -X POST http://localhost:8000/backup/schedules/cluster/1 \
  -d '{"job_type": "backup_full", "inventory_group_id": 1, "repository": "s3_repo", "cadence": "0 1 * * 0"}'    # Sundays at 1 AM

curl -s -H "Authorization: Bearer $STARROCKS_BR_API_KEY" -H "Content-Type: application/json" \
  -X POST http://localhost:8000/backup/schedules/cluster/1 \
  -d '{"job_type": "backup_incremental", "inventory_group_id": 1, "repository": "s3_repo", "cadence": "0 1 * * 1-6"}'    # Mon-Sat at 1 AM
```

Jobs never start on their own: submitting one (a schedule becoming due, a one-shot schedule, a
restore, a schedule deletion) only queues it as `PENDING`. A scheduler **tick** starts queued jobs, so
something must run a tick on a short, fixed interval. Each tick reconciles jobs whose worker died, queues
the schedules that are due, and then starts queued jobs: at most one job per cluster at a time (restore
first, then backups, then other work), while different clusters run in parallel. A job waiting for its
cluster simply stays `PENDING`; it does not fail.

```bash
# crontab: run a tick every minute
* * * * * STARROCKS_BR_DATABASE_URL=... STARROCKS_BR_DB_ENCRYPTION_KEY=... starrocks-br-scheduler tick
```

```yaml
# Kubernetes CronJob, same idea (use the image that contains starrocks-br)
apiVersion: batch/v1
kind: CronJob
metadata:
  name: starrocks-br-scheduler
spec:
  schedule: "* * * * *"
  concurrencyPolicy: Allow
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: tick
            image: starrocks-br
            command: ["starrocks-br-scheduler", "tick"]
            envFrom:
            - secretRef:
                name: starrocks-br-config
          restartPolicy: OnFailure
```

The tick process runs the jobs it starts and stays alive until they finish (a long backup keeps that one
process running), so the next tick, which only takes the scheduler lock briefly, can start jobs on other
clusters. A tick that finds the lock held exits with code 75. `POST /backup/schedules/run` only queues due
schedules; it does not start jobs, so a tick is still needed.

If no tick runs, nothing runs, restores included. `GET /health` reports when the last tick completed.

## Monitoring Backups

### Check Recent Backup Status

```sql
SELECT
  label,
  backup_type,
  status,
  started_at,
  finished_at,
  error_message
FROM ops.backup_history
ORDER BY started_at DESC
LIMIT 10;
```

### Find Failed Backups

```sql
SELECT
  label,
  backup_type,
  status,
  error_message,
  started_at
FROM ops.backup_history
WHERE status = 'FAILED'
ORDER BY started_at DESC;
```

### Check Active Jobs

```sql
-- Check for active backups
SHOW BACKUP;

-- Check run status locks
SELECT scope, label, state, started_at
FROM ops.run_status
WHERE state = 'ACTIVE';
```

### Monitor Backup Success Rate

```sql
SELECT
  backup_type,
  COUNT(*) as total,
  SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END) as successful,
  SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) as failed
FROM ops.backup_history
WHERE started_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
GROUP BY backup_type;
```

## Monitoring Restores

### Check Recent Restore Operations

```sql
SELECT
  restore_label,
  target_backup,
  status,
  started_at,
  finished_at,
  error_message
FROM ops.restore_history
ORDER BY started_at DESC
LIMIT 10;
```

### Find Failed Restores

```sql
SELECT
  restore_label,
  target_backup,
  error_message,
  started_at
FROM ops.restore_history
WHERE status = 'FAILED'
ORDER BY started_at DESC;
```

## Alerting

Set up alerts based on backup status. Here are example queries for your monitoring system:

**Alert on failed backups:**
```sql
SELECT COUNT(*) as failed_backups
FROM ops.backup_history
WHERE status = 'FAILED'
  AND started_at >= DATE_SUB(NOW(), INTERVAL 24 HOUR);
```

**Alert on missing backups:**
```sql
SELECT
  CASE
    WHEN MAX(started_at) < DATE_SUB(NOW(), INTERVAL 24 HOUR)
    THEN 1
    ELSE 0
  END as backup_missing
FROM ops.backup_history;
```

## Retention Management

The tool logs all operations but doesn't automatically clean up old records. Consider periodic cleanup:

```sql
-- Delete backup history older than 90 days
DELETE FROM ops.backup_history
WHERE started_at < DATE_SUB(NOW(), INTERVAL 90 DAY);

-- Delete restore history older than 90 days
DELETE FROM ops.restore_history
WHERE started_at < DATE_SUB(NOW(), INTERVAL 90 DAY);
```

**Note:** This only cleans metadata in the `ops` database. Manage actual backup data in your repository (S3/HDFS) using your storage provider's lifecycle policies.

## Next Steps

- [API Server](api.md)
- [Core Concepts](core-concepts.md)
