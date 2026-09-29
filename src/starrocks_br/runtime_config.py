"""Timing settings shared by the job backend and the scheduler tick, read from the environment.

Kept outside `api/` so `jobs/` and the scheduler CLI can use it without importing the HTTP layer.
"""

import os

HEARTBEAT_ENV_VAR = "STARROCKS_BR_JOB_HEARTBEAT_SECONDS"
STALE_ENV_VAR = "STARROCKS_BR_JOB_STALE_SECONDS"
LOCK_TIMEOUT_ENV_VAR = "STARROCKS_BR_SCHEDULER_LOCK_TIMEOUT_SECONDS"
RETENTION_MAX_ENV_VAR = "STARROCKS_BR_RETENTION_MAX_SECONDS"

DEFAULT_HEARTBEAT_SECONDS = 30
DEFAULT_STALE_SECONDS = 180
DEFAULT_LOCK_TIMEOUT_SECONDS = 300
DEFAULT_RETENTION_MAX_SECONDS = 1800

# A job is only judged stale after this many heartbeat intervals have passed without a write,
# so a couple of failed or delayed heartbeat writes never look like a dead owner.
MIN_STALE_TO_HEARTBEAT_RATIO = 3


class InvalidTimingConfigError(ValueError):
    pass


def _positive_int(name: str, default: int | None) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as e:
        raise InvalidTimingConfigError(f"{name} must be an integer, got {raw!r}") from e
    if value < 1:
        raise InvalidTimingConfigError(f"{name} must be at least 1, got {value}")
    return value


def get_job_heartbeat_seconds() -> int:
    return _positive_int(HEARTBEAT_ENV_VAR, DEFAULT_HEARTBEAT_SECONDS)


def get_job_stale_seconds() -> int:
    """Seconds without a heartbeat after which a job is considered stale.

    Defaults to 180, raised to 3x the heartbeat interval when that interval is configured
    larger than 60s; an explicitly configured value below 3x the heartbeat is rejected.
    """
    heartbeat = get_job_heartbeat_seconds()
    minimum = heartbeat * MIN_STALE_TO_HEARTBEAT_RATIO

    configured = _positive_int(STALE_ENV_VAR, None)
    if configured is None:
        return max(DEFAULT_STALE_SECONDS, minimum)
    if configured < minimum:
        raise InvalidTimingConfigError(
            f"{STALE_ENV_VAR} ({configured}) must be at least {MIN_STALE_TO_HEARTBEAT_RATIO}x "
            f"{HEARTBEAT_ENV_VAR} ({heartbeat}), i.e. {minimum}"
        )
    return configured


def get_scheduler_lock_timeout_seconds() -> int:
    return _positive_int(LOCK_TIMEOUT_ENV_VAR, DEFAULT_LOCK_TIMEOUT_SECONDS)


def get_retention_max_seconds() -> int:
    """How long a retention job may keep starting new snapshot drops."""
    return _positive_int(RETENTION_MAX_ENV_VAR, DEFAULT_RETENTION_MAX_SECONDS)
