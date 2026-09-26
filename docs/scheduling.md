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

Then point a single cron entry (or Kubernetes CronJob) at the schedule-runner endpoint, on a
short, fixed interval — it checks what's due and triggers it, doing nothing otherwise:

```bash
# crontab: check every minute for due schedules
* * * * * curl -s -H "Authorization: Bearer $STARROCKS_BR_API_KEY" -X POST https://api.internal/backup/schedules/run
```

```yaml
# Kubernetes CronJob, same idea
apiVersion: batch/v1
kind: CronJob
metadata:
  name: starrocks-br-schedule-runner
spec:
  schedule: "* * * * *"
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: run-due
            image: curlimages/curl
            command:
            - sh
            - -c
            - "curl -sf -H \"Authorization: Bearer $STARROCKS_BR_API_KEY\" -X POST $STARROCKS_BR_API_URL/backup/schedules/run"
            env:
            - name: STARROCKS_BR_API_URL
              value: "https://api.internal"
            - name: STARROCKS_BR_API_KEY
              valueFrom:
                secretKeyRef:
                  name: starrocks-br-api-credentials
                  key: api-key
          restartPolicy: OnFailure
```

The actual backup work runs inside the long-lived API server process (via its configured job
execution backend), not inside this short-lived `run-due` invocation, so the cron job itself
finishes immediately regardless of how long the triggered backup takes.

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
