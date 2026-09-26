## 1. Document the architecture

- [x] 1.1 Update the root `AGENTS.md` with the StarRocks backup and restore purpose, inventory groups, repositories, schedules, and manual operations; verify the description matches `README.md` and the command/API entry points.
- [x] 1.2 Document the entry-point, commands, core-operation, and metadata-layer responsibilities and dependency direction; verify package names and representative call paths against `src/starrocks_br/cli.py`, `src/starrocks_br/api/`, `src/starrocks_br/commands/`, and `src/starrocks_br/jobs/`.
- [x] 1.3 Describe SQLAlchemy/Alembic persistence, the intended short-lived metadata transactions, and concurrency accurately; verify actual session lifetime in `src/starrocks_br/commands/backup.py` and `src/starrocks_br/executor.py`, and concurrency behavior in `src/starrocks_br/concurrency.py` and `src/starrocks_br/jobs/thread_backend.py`.
- [x] 1.4 Link API guidance to the API specs and implementation, and identify known command-boundary exceptions; verify every referenced path exists and the exceptions match current route/CLI calls.

## 2. Review the documentation change

- [x] 2.1 Review the `AGENTS.md` diff to confirm it documents architecture only, distinguishes intended boundaries from current behavior, and makes no runtime or API changes.
