## REMOVED Requirements

### Requirement: CLI commands authenticate against a configured API server
**Reason**: The `starrocks-br api ...` CLI subcommands are removed entirely as part of dropping the CLI layer; the HTTP API is now accessed directly.
**Migration**: Call the FastAPI server's endpoints directly (e.g. via `curl` or an HTTP client), authenticating with the `X-API-Key` header as documented in `docs/api.md`.

### Requirement: CLI can manage the cluster registry
**Reason**: The CLI layer that wrapped these API calls is removed; the underlying cluster-registry endpoints in the `api-cluster-registry` capability are unaffected and remain the way to register, list, and remove clusters.
**Migration**: Call the cluster registry endpoints directly over HTTP instead of the CLI's cluster commands.

### Requirement: CLI can submit and monitor jobs via the API
**Reason**: The CLI layer that wrapped these API calls is removed; the underlying job-submission and job-status endpoints in the `api-job-execution` capability are unaffected. Group-name-to-id resolution and wait/poll behavior were CLI conveniences, not API guarantees.
**Migration**: Submit jobs with the inventory group's numeric id directly via the API, and poll the job-status endpoint from the calling script or tool instead of relying on CLI wait/poll behavior.

### Requirement: CLI can manage schedules against a specific cluster
**Reason**: The CLI layer that wrapped these API calls is removed; the underlying schedule endpoints in the `api-scheduling` capability are unaffected. Group-name-to-id resolution was a CLI convenience, not an API guarantee.
**Migration**: Create, list, update, and delete schedules directly via the cluster-scoped schedule endpoints, using the inventory group's numeric id.

### Requirement: CLI provides a single-shot schedule runner for cron/CronJob use
**Reason**: The CLI wrapper around the run-due-schedules endpoint is removed.
**Migration**: Invoke the API's run-due-schedules endpoint directly (e.g. with `curl`) from cron or a Kubernetes CronJob, checking the HTTP status code for failure instead of a CLI exit code.
