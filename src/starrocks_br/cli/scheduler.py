"""`starrocks-br-scheduler tick`: run one scheduler tick (including job dispatch) and exit.

Meant to be invoked by cron, a systemd timer or a Kubernetes CronJob, which supply the cadence;
this process has no loop, no sleep and no enable switch. Like the HTTP routes, it calls only into
the commands layer. Exit codes: 0 tick ran, 1 tick failed, 75 (EX_TEMPFAIL) another tick holds
the lock, 78 (EX_CONFIG) missing or invalid configuration.
"""

import argparse
import os
import sys
from collections.abc import Sequence

from .. import logger
from ..commands import schedules as schedules_commands
from ..jobs.backend import get_registry, set_registry
from ..jobs.bootstrap import UnimplementedBackendError, build_backend_registry
from ..runtime_config import InvalidTimingConfigError, get_job_stale_seconds
from ..store.crypto import ENCRYPTION_KEY_ENV_VAR

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_TEMPFAIL = 75
EXIT_CONFIG = 78

_ALREADY_RUNNING = "scheduler already running"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="starrocks-br-scheduler",
        description="Run the StarRocks backup scheduler for a single tick.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "tick",
        help="reconcile stale jobs, run due schedules, one-shot expiry and job dispatch once, then exit",
    )
    return parser


def _configure() -> None:
    """Prepare what the API server prepares at startup; raises `_ConfigError` if unusable."""
    if not os.getenv(ENCRYPTION_KEY_ENV_VAR):
        raise _ConfigError(f"{ENCRYPTION_KEY_ENV_VAR} must be set to decrypt cluster passwords")
    try:
        get_job_stale_seconds()
        set_registry(build_backend_registry())
    except (InvalidTimingConfigError, UnimplementedBackendError) as e:
        raise _ConfigError(str(e)) from e


class _ConfigError(Exception):
    pass


def _wait_for_workers() -> None:
    """Block until every in-process worker dispatched during the tick has finished.

    The scheduler lock is already released by now, so a long backup never blocks another tick;
    without this wait, a container would be torn down when this process exits, killing the
    backups it just started.
    """
    registry = get_registry()
    for name in registry.enabled_backends:
        shutdown = getattr(registry.get(name), "shutdown", None)
        if shutdown is not None:
            shutdown(wait=True)


def _run_tick() -> int:
    try:
        result = schedules_commands.execute_scheduler_tick()
    except Exception as e:
        logger.error(f"Scheduler tick failed: {e}")
        return EXIT_FAILED

    if not result.acquired:
        print(_ALREADY_RUNNING, file=sys.stderr)
        return EXIT_TEMPFAIL

    recon = result.reconciliation
    dispatch = result.dispatch
    logger.info(
        f"Scheduler tick complete: {len(result.triggered_job_ids)} schedule(s) triggered, "
        f"{len(result.cleanup_job_ids)} expiry cleanup(s) submitted, "
        f"{len(dispatch.admitted)} job(s) started, {len(dispatch.failed)} job(s) failed to start, "
        f"{len(recon.failed)} stale job(s) failed"
    )
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        _configure()
    except _ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return EXIT_CONFIG

    if args.command == "tick":
        try:
            return _run_tick()
        finally:
            _wait_for_workers()
    return EXIT_FAILED  # unreachable: argparse rejects unknown commands


if __name__ == "__main__":
    sys.exit(main())
