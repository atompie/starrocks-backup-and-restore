# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **BREAKING: Ops bookkeeping moved off StarRocks, into this tool's own SQLite metastore.**
  `table_inventory`, `backup_history`, `restore_history`, `run_status`, and
  `backup_partitions` no longer live in a per-cluster StarRocks database
  (previously named via `ops_database`, default `"ops"`) - they live in the
  same metadata store the API server already uses for `clusters`/`jobs`/
  `schedules`, scoped by `cluster_id`. This fixes bookkeeping being
  unreachable exactly when a StarRocks cluster is down or corrupted (the
  situation where you most need to know what backups exist), and removes an
  internal implementation detail from the user-facing surface.
- **BREAKING: `ops_database` removed entirely** from `POST /cluster`,
  `PATCH /cluster/{id}`, `ClusterRead` responses, the `cli_api cluster add
  --ops-database` flag, and the standalone CLI's YAML config. A request body
  still containing it is silently ignored, not rejected.
- **BREAKING: `alembic upgrade head` (against `STARROCKS_BR_DATABASE_URL`) and
  `STARROCKS_BR_DB_ENCRYPTION_KEY` are now required for the standalone CLI
  too**, not just the API server - `starrocks-br init` registers the config's
  cluster in that metastore and bootstraps its table inventory; every other
  CLI command requires `init` to have been run first for that config and
  fails clearly (rather than silently registering a cluster) if not.
- Incidentally fixes a pre-existing SQL-injection-shaped bug in `prune.py`'s
  `get_successful_backups`/`cleanup_backup_history` (unquoted string
  interpolation) as a side effect of the ORM conversion; the same pattern in
  `verify_snapshot_exists`/`execute_drop_snapshot` (StarRocks-only, unrelated
  to ops bookkeeping) is unchanged and remains a known issue.

### Migration notes
- No production data migration is provided or needed for this change (no ops
  data existed in supported deployments yet). Run `alembic upgrade head`
  against your metastore, then re-run `starrocks-br init` for each
  standalone-CLI-managed cluster before its next backup/restore/prune.

## [0.7.0a1] - 2026-02-04 (Alpha)

> **Note**: This is an alpha release. The `prune` command requires StarRocks with `DROP SNAPSHOT` support, which is not yet available upstream.

### Added
- **New Command**: `prune` - Manage backup lifecycle with flexible retention policies
  - Multiple pruning strategies: `--keep-last`, `--older-than`, `--snapshot`, `--snapshots`
  - Group-specific pruning with `--group` filter
  - Dry-run mode (`--dry-run`) to preview deletions before executing
  - Auto-confirmation with `--yes` flag for automation
  - Automatically cleans up backup history and partition metadata after deletion
  - Comprehensive documentation with integration testing guide
- **Test Coverage**: 52 comprehensive tests (31 unit tests + 21 integration tests) for prune command
  - 100% code coverage for `prune.py` module

## [0.6.0] - 2026-02-04

### Added
- **Configurable ops database name**: Customize the internal tracking database name via `ops_database` configuration option
- **Table inventory support in config.yaml**: Define table groups directly in configuration file using `table_inventories` section
- **Repository validation in init command**: The `init` command now validates that the repository specified in config.yaml actually exists in StarRocks
- **Database existence warnings**: Configuration loading now warns when a database specified in table inventory doesn't exist

### Fixed
- **Incremental backup restore**: Fixed partition-level restore for incremental backups to correctly apply partition data
- **Missing table restore**: Fixed restore to properly handle tables that only exist in incremental backups but not in the full backup
- **Planner error messages**: Display clear error messages when tables within a table inventory do not exist

## [0.5.2] - 2025-12-09

### Fixed
- **Critical Bug**: Fixed backup tracking failure caused by backtick mismatch
  - Backup commands use backticks for SQL identifier quoting (e.g., `` `label` ``)
  - StarRocks SHOW BACKUP returns labels without backticks (e.g., `label`)
  - Label extraction now strips backticks to match SHOW BACKUP output
  - Affects both full and incremental backup operations
  - Prevents false "Backup tracking lost" errors when no concurrency issue exists

## [0.5.1] - 2025-11-21

### Added
- Enhanced logging coverage across additional modules

### Changed
- Split monolithic test_cli.py into focused, modular test files for better maintainability
- Improved release process to use git tag messages in releases

## [0.5.0] - 2025-11-20

### Added
- Modern logging system with rich error handling and colored output
- Pre-commit hooks for automated code quality checks (ruff)
- Support for Python 3.10, 3.11, and 3.12

### Changed
- **Breaking**: Dropped Python 3.9 support (EOL)
- Minimum required Python version is now 3.10
- Improved error messages with visual indicators
- Enhanced developer experience with automatic linting and formatting

### Fixed
- Test compatibility with new logging system
- CI workflow now tests on Python 3.10, 3.11, and 3.12

## [0.4.0] - 2025-11-20

### Added
- SQL identifier sanitization with `quote_identifier()` and `quote_value()` helpers
- Exponential backoff for polling operations (1s to 60s max interval)

### Changed
- All SQL queries now use backtick quoting to protect against SQL injection
- Restore operations now use cluster timezone instead of local `datetime.now()`
- Job slot reservation now happens before recording backup partitions

### Fixed
- **Security**: SQL injection protection for database/table/partition identifiers
- **Reliability**: Prevents orphaned records when job slot reservation fails
- **Performance**: Reduced database polling from ~21,600 to ~300 polls for 6-hour operations
- **Correctness**: Eliminated timestamp drift across different machines in restore operations

## [0.3.0] - 2025-11-18

### Fixed
- **Critical Fix**: Resolved StarRocks 128-byte PRIMARY KEY size limit issue for `ops.backup_partitions` table
  - Changed `backup_partitions` table to use hash-based PRIMARY KEY (`key_hash`) instead of composite key
  - Composite keys (label + database_name + table_name + partition_name) can now exceed 128 bytes without errors
  - Implemented MD5 hashing for partition tracking to bypass size restrictions
  - Updated `record_backup_partitions()` to automatically compute MD5 hash of composite keys

### Changed
- **Schema Migration**: `ops.table_inventory` now uses UNIQUE KEY instead of PRIMARY KEY
  - Prevents potential size limit issues with long database/table names
  - Maintains backward-compatible INSERT behavior for users
  - No manual hash computation required for table inventory operations

### Technical Details
- Hash-based approach allows unlimited composite key sizes (248+ bytes tested)
- Old limit: 128 bytes for composite PRIMARY KEY
- New approach: 32-byte MD5 hash as PRIMARY KEY
- Distribution strategy: `DISTRIBUTED BY HASH(key_hash)` for backup_partitions
- Distribution strategy: `DISTRIBUTED BY HASH(inventory_group)` for table_inventory

### Migration Notes
⚠️ **Breaking Change**: This is a schema-breaking change for existing deployments.

Existing users must:
1. Drop and recreate `ops.backup_partitions` table (or add `key_hash` column and populate)
2. Drop and recreate `ops.table_inventory` table with UNIQUE KEY schema
3. Re-run `starrocks-br init` to apply the new schema

Alternatively, run:
```sql
DROP DATABASE ops;
```
Then re-initialize with `starrocks-br init --config config.yaml`

### Testing
- All 328 unit tests pass
- Integration test suite added for primary key size limit validation
- Test files: `test_pk_limit_fix.sh`, `MANUAL_TEST_GUIDE.md`, `RUN_INTEGRATION_TEST.md`

## [0.2.0] - 2025-11-17

### Added
- Initial public release
- Full and incremental backup support
- Intelligent point-in-time restore
- Inventory group management
- Automatic backup chain resolution
- Concurrency control via job slots
- Comprehensive error handling and logging

### Features
- Multi-table backup with wildcard support
- Partition-level incremental backups
- Atomic restore with temporary table renaming
- TLS/SSL connection support
- Cluster health validation
- Repository verification

## [0.1.0] - 2025-10-30

### Added
- Initial development version
- Core backup and restore functionality
- Schema initialization
- Basic CLI interface
