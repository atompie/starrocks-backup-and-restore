import ast
import datetime
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from starrocks_br.cli import scheduler as cli
from starrocks_br.commands import jobs as jobs_commands
from starrocks_br.commands import schedules as schedules_commands
from starrocks_br.jobs.backend import reset_registry
from starrocks_br.store import crypto as crypto_module
from starrocks_br.store import session as session_module
from starrocks_br.store.models import Base

ENCRYPTION_KEY = "0" * 43 + "="


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("STARROCKS_BR_DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setenv("STARROCKS_BR_DB_ENCRYPTION_KEY", ENCRYPTION_KEY)
    monkeypatch.setenv("STARROCKS_BR_ENABLED_BACKENDS", "thread")
    monkeypatch.setenv("STARROCKS_BR_DEFAULT_BACKEND", "thread")
    session_module.reset_engine_cache()
    crypto_module.reset_key_cache()
    reset_registry()
    Base.metadata.create_all(session_module.get_engine())
    yield
    session_module.reset_engine_cache()
    crypto_module.reset_key_cache()
    reset_registry()


@pytest.fixture
def tick_steps(mocker):
    """Stub the four things a tick does so the CLI can be tested in isolation."""
    mocker.patch.object(schedules_commands, "reconcile_stale_jobs", return_value=jobs_commands.ReconciliationSummary())
    mocker.patch.object(schedules_commands, "run_due_schedules", return_value=([1, 2], 2))
    mocker.patch.object(schedules_commands, "expire_due_schedules", return_value=[3])
    mocker.patch.object(
        schedules_commands, "dispatch_pending_jobs", return_value=jobs_commands.DispatchSummary(admitted=[4])
    )


def test_tick_exits_zero_and_records_the_tick(env, tick_steps):
    assert cli.main(["tick"]) == 0

    with session_module.session_scope() as session:
        assert schedules_commands.get_scheduler_last_tick_at(session) is not None


def test_tick_releases_the_lock_when_done(env, tick_steps):
    cli.main(["tick"])

    assert schedules_commands.try_acquire_scheduler_lock("someone-else").acquired is True


def test_contention_exits_75_with_message_and_does_nothing(env, tick_steps, capsys):
    schedules_commands.try_acquire_scheduler_lock("other-host:1")

    code = cli.main(["tick"])

    assert code == 75
    assert "scheduler already running" in capsys.readouterr().err
    schedules_commands.run_due_schedules.assert_not_called()
    schedules_commands.expire_due_schedules.assert_not_called()
    schedules_commands.reconcile_stale_jobs.assert_not_called()
    with session_module.session_scope() as session:
        assert schedules_commands.get_scheduler_last_tick_at(session) is None


def test_stale_lock_is_reclaimed_with_a_warning(env, tick_steps, monkeypatch, mocker):
    monkeypatch.setenv("STARROCKS_BR_SCHEDULER_LOCK_TIMEOUT_SECONDS", "1")
    schedules_commands.try_acquire_scheduler_lock("crashed-host:1")
    later = schedules_commands._utcnow() + datetime.timedelta(seconds=10)
    mocker.patch.object(schedules_commands, "_utcnow", return_value=later)
    warning = mocker.patch.object(schedules_commands.logger, "warning")

    assert cli.main(["tick"]) == 0

    assert any("stale" in call.args[0].lower() for call in warning.call_args_list)
    schedules_commands.run_due_schedules.assert_called_once()


def test_failed_tick_exits_one_and_releases_the_lock(env, tick_steps, mocker):
    mocker.patch.object(schedules_commands, "run_due_schedules", side_effect=RuntimeError("boom"))

    assert cli.main(["tick"]) == 1

    assert schedules_commands.try_acquire_scheduler_lock("someone-else").acquired is True
    with session_module.session_scope() as session:
        assert schedules_commands.get_scheduler_last_tick_at(session) is None


def test_waits_for_worker_threads_after_the_lock_is_released(env, tick_steps, mocker):
    order = []
    backend = MagicMock()
    backend.shutdown.side_effect = lambda wait: order.append(("shutdown", wait))
    registry = MagicMock(enabled_backends=["thread"])
    registry.get.return_value = backend
    mocker.patch.object(cli, "set_registry")
    mocker.patch.object(cli, "get_registry", return_value=registry)
    real_release = schedules_commands.release_scheduler_lock
    mocker.patch.object(
        schedules_commands,
        "release_scheduler_lock",
        side_effect=lambda holder=None: order.append(("release", None)) or real_release(holder),
    )

    assert cli.main(["tick"]) == 0

    assert order == [("release", None), ("shutdown", True)]


def test_workers_are_awaited_even_when_the_tick_fails(env, tick_steps, mocker):
    backend = MagicMock()
    registry = MagicMock(enabled_backends=["thread"])
    registry.get.return_value = backend
    mocker.patch.object(cli, "set_registry")
    mocker.patch.object(cli, "get_registry", return_value=registry)
    mocker.patch.object(schedules_commands, "run_due_schedules", side_effect=RuntimeError("boom"))

    cli.main(["tick"])

    backend.shutdown.assert_called_once_with(wait=True)


def test_missing_encryption_key_is_a_configuration_error(env, monkeypatch, capsys):
    monkeypatch.delenv("STARROCKS_BR_DB_ENCRYPTION_KEY")

    assert cli.main(["tick"]) == 78
    assert "STARROCKS_BR_DB_ENCRYPTION_KEY" in capsys.readouterr().err


def test_invalid_stale_threshold_is_a_configuration_error(env, monkeypatch, capsys):
    monkeypatch.setenv("STARROCKS_BR_JOB_HEARTBEAT_SECONDS", "30")
    monkeypatch.setenv("STARROCKS_BR_JOB_STALE_SECONDS", "10")

    assert cli.main(["tick"]) == 78


def test_unknown_subcommand_is_rejected():
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["nonsense"])

    assert excinfo.value.code == 2


def test_cli_imports_only_the_commands_layer():
    """Like the HTTP routes, the CLI must not reach core operation modules or the DAL directly."""
    forbidden = {"planner", "executor", "restore", "prune", "reconcile", "concurrency", "db", "dal"}
    tree = ast.parse(Path(cli.__file__).read_text())

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level >= 2:
            imported.add((node.module or "").split(".")[0])
            imported.update(alias.name for alias in node.names if not node.module)

    assert imported.isdisjoint(forbidden), imported & forbidden


def test_python_dash_m_invocation_runs_a_tick(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'sub.db'}"
    env = {
        **os.environ,
        "STARROCKS_BR_DATABASE_URL": db_url,
        "STARROCKS_BR_DB_ENCRYPTION_KEY": ENCRYPTION_KEY,
        "PYTHONPATH": str(Path(cli.__file__).resolve().parents[2]),
    }
    repo_root = Path(cli.__file__).resolve().parents[3]
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=repo_root, env=env, check=True, capture_output=True
    )

    proc = subprocess.run(
        [sys.executable, "-m", "starrocks_br.cli.scheduler", "tick"], env=env, capture_output=True, text=True
    )

    assert proc.returncode == 0, proc.stderr


def test_tick_logs_how_many_jobs_it_started(env, tick_steps, mocker):
    info = mocker.patch.object(cli.logger, "info")

    assert cli.main(["tick"]) == 0

    assert any("1 job(s) started" in call.args[0] for call in info.call_args_list)
