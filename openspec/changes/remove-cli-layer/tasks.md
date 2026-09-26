## 1. Remove CLI-only tests

- [x] 1.1 Delete `tests/unit/service/test_cli_backup.py`, `test_cli_restore.py`, `test_cli_init.py`, `test_cli_general.py`, `test_cli_exceptions.py`, `test_prune_cli.py`, `test_cli_api_client.py`, `test_api_cli_parity.py`, `test_error_handler.py`, `test_config.py`, and verify `pytest` still collects the remaining suite without import errors. (`test_api_cli_parity.py` no longer existed under that name — it was already renamed to `test_backup_dispatch_parity.py`, which tests manual-vs-scheduled API dispatch parity, not CLI, and is out of scope.)

## 2. Remove CLI source

- [x] 2.1 Delete `src/starrocks_br/cli.py` and verify `grep -rn "starrocks_br.cli\b\|from .cli import\|from starrocks_br import cli" src/ tests/` returns no hits. (Also removed the two CLI-only fixtures in `tests/conftest.py` — `mock_resolved_cluster` and `mock_cluster_not_initialized` — which patched `starrocks_br.cli` and had no remaining callers once the CLI test files were deleted.)
- [x] 2.2 Delete `src/starrocks_br/cli_api/` and verify `grep -rn "cli_api" src/ tests/` returns no hits.
- [x] 2.3 Delete `src/starrocks_br/error_handler.py` and verify `grep -rn "error_handler" src/ tests/` returns no hits.
- [x] 2.4 Delete `entry_point.py` and verify `grep -rln "entry_point" . --include=*.py --include=*.toml --include=*.yml` (excluding `.venv`) returns no hits.
- [x] 2.5 Delete `src/starrocks_br/config.py` (top-level YAML-config module) and verify `grep -rn "from \. import config\|from starrocks_br import config\b\|starrocks_br\.config\b" src/ tests/` returns no hits, and that `src/starrocks_br/api/config.py` is untouched.
- [x] 2.6 Remove `bootstrap_table_inventory` from `src/starrocks_br/inventory_groups.py` after confirming with `grep -rn "bootstrap_table_inventory" src/ tests/` that no remaining caller exists, then remove that grep's last hit too. (Also removed `_get_or_create_group_id`, a private helper used only by `bootstrap_table_inventory`, and its 4 dedicated tests in `tests/unit/crud/test_inventory_groups_sql.py`, which tasks.md didn't name individually but were needed to keep the suite green.)
- [x] 2.7 Run the full test suite (`pytest`) and confirm it passes with zero collection errors. (Full suite green: all tests pass.)

## 3. Update packaging and CI

- [x] 3.1 In `pyproject.toml`, remove the `[project.scripts]` section (the `starrocks-br` console script).
- [x] 3.2 In `pyproject.toml`, remove the `click` dependency from `dependencies` and confirm `grep -rn "^import click\|^from click" src/` returns no hits. (Also dropped `PyYAML`, which was only used by the now-deleted `config.py`'s `load_config` — same dead-dependency cleanup, confirmed with `grep -rn "^import yaml\|^from yaml" src/`.)
- [x] 3.3 In `pyproject.toml`, move the packages currently listed under `[project.optional-dependencies] api` into the base `dependencies` list, and remove the now-unneeded `api` extras group.
- [x] 3.4 Run `pip install -e .` in a clean virtualenv (or equivalent dependency resolution check) and verify the install succeeds with the updated dependency list. (Ran in the project's existing `.venv`; install succeeded.)
- [x] 3.5 Delete `.github/workflows/build-executables.yml` and verify no other workflow references it (`grep -rln "build-executables" .github/`). (Only `ci.yml` remains and has no CLI references.)

## 4. Update documentation

All 7 files under `docs/`, plus `README.md` and `AGENTS.md`, contain CLI references and need
updating — not just `README.md` and `docs/commands.md` as originally scoped. Bounded scope for
this group: remove every CLI command/example and replace it with its HTTP/API equivalent (curl or
prose) wherever the CLI was the actual instruction being given. Do not attempt to fix unrelated
pre-existing documentation staleness (e.g. `docs/scheduling.md` and `docs/getting-started.md`
querying an `ops.*` database that a prior, separate change already moved into this tool's own
metastore) — flag such staleness as a known gap on completion rather than fixing it here.

- [x] 4.1 Delete `docs/commands.md` (pure CLI command reference, including an "API Server" section
      redundant with `docs/api.md`'s own Quickstart) and fix every link to it (`README.md`,
      `docs/installation.md`'s `commands.md#api-server` link, `docs/getting-started.md`,
      `docs/scheduling.md`, `docs/core-concepts.md`) to point at `docs/api.md` instead.
- [x] 4.2 Rewrite `docs/api.md`: drop the "existing direct-to-StarRocks CLI commands ... work
      exactly as before" framing and the "How It Fits Together" diagram's CLI mentions; update
      Installation to reflect the API being a base dependency (no more `[api]` extra); replace the
      Quickstart's `starrocks-br init`/`starrocks-br api cluster add`/`starrocks-br api job submit`
      steps with equivalent curl calls; remove the entire "CLI Reference" section; update
      Troubleshooting entries that mention the CLI reading environment variables. (Also fixed two
      pre-existing field-name inaccuracies in the same "Manual Backups"/"Backup Schedules" tables I
      was already rewriting — `group`/`group_name` in the docs vs. the actual `group_id`/
      `inventory_group_id` schema fields — since leaving them would make the just-corrected
      Quickstart contradict the reference tables two sections later in the same file.)
- [x] 4.3 Rewrite `README.md`'s Installation and Basic Usage sections to describe installing and
      running the API server (e.g. via `uvicorn`) and interacting with it over HTTP with curl
      examples, removing all `starrocks-br <command>` CLI examples, and fix the Documentation
      links list (drop the `docs/commands.md` link). (Left the "Why This Tool?" and "How It Works"
      sections' `ops` database mentions untouched — pre-existing staleness from an earlier,
      separate change that moved bookkeeping into this tool's own metastore, unrelated to CLI
      removal; flagging in the final report rather than fixing here.)
- [x] 4.4 Rewrite `docs/getting-started.md` as an API-based walkthrough: register a cluster,
      create an inventory group, submit a full backup, and restore, all via curl against the
      running API server, replacing the `config.yaml`/`starrocks-br init`/`starrocks-br backup`/
      `starrocks-br restore` walkthrough. Kept the StarRocks-side repository-creation SQL (that
      part is unrelated to the CLI).
- [x] 4.5 Rewrite `docs/installation.md`: drop the standalone-executable installation option
      (Option 2) and the CLI-specific `starrocks-br --help` verification steps, since there is no
      console script; keep the PyPI/micromamba/devbox/manual-dev-setup paths but verify install
      with something that doesn't assume a CLI (e.g. `python -c "import starrocks_br"` or starting
      the API server); drop the "optional API extra" framing since API dependencies are now base.
- [x] 4.6 Rewrite `docs/configuration.md`: remove the `config.yaml`-based configuration sections
      (Basic Configuration, Table Inventory Configuration, `STARROCKS_PASSWORD`-for-CLI, and the
      `starrocks-br init` breaking-change note) since that config file and its CLI reader no
      longer exist; keep the Repository Setup SQL section; keep and lead with the existing "API
      Server Configuration" section as the primary configuration reference, dropping its
      CLI-specific `STARROCKS_BR_API_URL`/`--api-url`/`--api-key` sub-section. Deviated from the
      literal task text on TLS: rather than keeping the old TLS YAML-section verbatim plus a note,
      I dropped it — the whole section was `config.yaml`'s `tls:` block syntax, and the API's
      `POST /cluster` schema has no TLS fields at all (checked `src/starrocks_br/api/schemas.py`),
      so keeping YAML syntax that no longer maps to anything would actively mislead readers into
      trying it in a JSON body. Replaced it with one line under "Registering a Cluster" stating
      TLS is not currently exposed through cluster registration.
- [x] 4.7 Rewrite `docs/scheduling.md`: remove the "Using Cron directly (no API server)" and
      "Using Kubernetes CronJob" (direct CLI cron) sections entirely, since there is no CLI to
      invoke; keep the "Recommended: API-managed schedules" section but replace its
      `starrocks-br api schedule add`/`run-due` commands with curl equivalents.
- [x] 4.8 Update `docs/core-concepts.md`: replace its ~8 illustrative `starrocks-br backup`/
      `starrocks-br restore` CLI snippets with equivalent curl/API illustrations, preserving the
      surrounding conceptual explanation unchanged. (Left "The ops Database" section's framing
      untouched — same pre-existing `ops`-database staleness as elsewhere, unrelated to CLI
      removal; flagged in the final report.)
- [x] 4.9 Update `AGENTS.md`: remove `src/starrocks_br/cli.py` and `src/starrocks_br/cli_api/`
      from "Main source areas", and remove the CLI-specific architectural-boundary exceptions
      ("the CLI resolves inventory-group names...", "CLI initialization also calls inventory and
      repository helpers directly") that no longer apply once the CLI is gone. (Also updated the
      "CLI / HTTP API" flow diagram and surrounding prose to say "HTTP API" only, and dropped "CLI"
      from the testing-conventions sentence listing which layers `tests/unit/service/` covers.)

## 5. Final verification

- [x] 5.0 A repo-wide grep for `starrocks-br `/`cli.py`/`cli_api` after group 4 surfaced several
      files outside the original plan that were left broken or stale by the group 2/3 deletions;
      fixed all of them since leaving them would contradict "API must work":
      - `run.sh` (the project's documented dev convenience script) installed with the now-removed
        `[api]` extra and `exec`'d the now-deleted `starrocks-br api serve` command — fixed to
        `pip install -e .` and `exec uvicorn starrocks_br.api.app:create_app --factory ...`.
      - `build_executable.sh` — a local PyInstaller build script for the CLI binary, referencing
        the now-deleted `entry_point.py`. Deleted (same rationale as removing
        `.github/workflows/build-executables.yml` in task 3.5 — nothing left to build).
      - `devbox.json` — its dev-shell init hook printed `starrocks-br --help` as a "getting
        started" hint. Changed to `./run.sh`.
      - `src/starrocks_br/exceptions.py` — `ClusterNotInitializedError`'s message told users to
        run `starrocks-br init --config <config.yaml>`, a deleted command. Investigated further:
        this exception, plus `MissingOptionError`, `ConfigFileNotFoundError`, and
        `ConfigValidationError`, were raised only by the now-deleted `cli.py`/`config.py`/
        `error_handler.py` and had zero remaining callers — removed all four as dead code.
      - `src/starrocks_br/commands/__init__.py`, `commands/backup.py`, `jobs/handlers.py`,
        `api/app.py` — docstrings/comments describing `cli.py` as a live sibling adapter to the
        API. Updated to describe the API as the sole adapter over the commands layer.
      Left untouched (unlinked from any living doc, historical/external in nature, same category
      as `CHANGELOG.md`): `TASK_4.md` (a completed-task planning note predating this change) and
      `starrocks-br-article.md` (a marketing article draft) both still contain CLI command
      examples — flagging rather than rewriting, consistent with the bounded-scope decision for
      other pre-existing staleness in task 4.x.
- [x] 5.1 Run the full test suite (`pytest`) and confirm it passes. (Full suite green.)
- [x] 5.2 Run `openspec validate remove-cli-layer --strict` (append `--store <id>` if applicable) and confirm it passes with no errors. (Valid.)
- [x] 5.3 Grep the repo (excluding `.venv` and `openspec/changes/archive`) for `starrocks-br `, `cli.py`, and `cli_api` to confirm no stray references remain in source, docs, or CI config outside of this change's own archived spec history. (Remaining hits are `CHANGELOG.md` (historical), `TASK_4.md`/`starrocks-br-article.md` (flagged above, out of bounded scope), this change's own planning artifacts, and incidental product-name/env-name matches in `run.sh`, `docs/installation.md`, `src/starrocks_br/api/app.py` that are not CLI invocations.)
