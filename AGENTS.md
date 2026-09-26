# Project Architecture

## Repository-wide rules

- Do not add licensing information to files, including license headers, copyright notices, or license text.

## Project purpose

This project provides a backup and restore system for StarRocks databases. Inventory groups define the database tables in scope for a backup. StarRocks repositories identify the backup storage destinations. Schedules describe when recurring backups should run, and users can also start backups manually. Backups are restored through StarRocks' backup and restore mechanisms, with this project coordinating the operation and tracking its metadata.

## Architectural boundaries

The main application flow is:

```text
CLI / HTTP API
      |
      v
 commands layer -----> core operations
      |                 (planner, executor, restore, prune, etc.)
      v
 data access layer
 (SQLAlchemy models and sessions; Alembic migrations)
```

- The CLI and HTTP API are entry points. For application operations, they should call the corresponding functions in `src/starrocks_br/commands/` rather than call core operation modules directly.
- The commands layer coordinates use cases and calls core operation modules. Commands may call other commands when composing a use case, as `commands/schedules.py` does when it submits jobs through `commands/jobs.py`.
- Core operation modules implement StarRocks work and domain logic. They must not call back into the commands layer.
- Persistent metadata has a separate data access layer in `src/starrocks_br/store/`. SQLAlchemy models and sessions provide database access; Alembic manages schema migrations. The metadata store defaults to SQLite and is configured through `STARROCKS_BR_DATABASE_URL`.

## Parallel execution and metadata

The job system can execute work concurrently across its worker pool. Keep long-running StarRocks operations separate from metadata transactions: metadata reads and writes should use short-lived sessions, and a database transaction should not remain open while StarRocks runs or a job polls for completion. Updates to job state, backup history, partition manifests, and concurrency reservations must remain safe when multiple jobs are active.

Current behavior has two important limits. `concurrency.reserve_job_slot` reserves the cluster's `backup` scope, so backup operations on the same cluster are currently serialized. Also, `commands/backup.py` keeps a SQLAlchemy session open while `executor.execute_backup` submits and polls StarRocks. Treat short transaction lifetimes and parallel-safe metadata as architectural requirements; these current behaviors are gaps to account for when changing backup execution. Keep concurrency policy explicit and separate from the backup operation itself.

Some existing paths do not yet follow the intended commands boundary: API routes perform inventory-group operations and pre-validation directly, while the CLI resolves inventory-group names before invoking backup, restore, and prune commands. CLI initialization also calls inventory and repository helpers directly. Treat these as current exceptions; when changing them, preserve existing behavior while moving application use cases behind commands where appropriate.

## API conventions

Follow the relevant API specifications in `openspec/specs/api-*/spec.md` and the established implementation patterns in `src/starrocks_br/api/` (routes, schemas, dependencies, and authentication). The specifications define endpoint behavior, validation, and response semantics; check the matching spec when changing an API capability.

Order API path segments from the domain or service, to the operation, then the required context and identifiers:

```text
/{domain-or-service}/{operation}/{context}/{identifiers}
```

For example: `POST /backup/manual/full/cluster/{cluster_id}` and `GET /backup/schedules/cluster/{cluster_id}`.

## Main source areas

- `src/starrocks_br/cli.py`: direct CLI adapter.
- `src/starrocks_br/cli_api/`: CLI client for the HTTP API.
- `src/starrocks_br/api/`: FastAPI application, routes, schemas, auth, and dependencies.
- `src/starrocks_br/commands/`: shared application use cases called by entry points and composed by other commands.
- `src/starrocks_br/jobs/`: asynchronous job backend interface, registry, and command handlers.
- `src/starrocks_br/`: StarRocks connection and core backup, restore, planning, execution, and pruning logic.
- `src/starrocks_br/store/`: SQLAlchemy metadata models, sessions, encryption, and Alembic migrations.
- `tests/`: automated tests organized around CLI, API, commands, and core behavior.
