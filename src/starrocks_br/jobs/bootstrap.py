"""Builds the job backend registry from environment configuration.

Shared by the API server and the scheduler CLI so both resolve `STARROCKS_BR_ENABLED_BACKENDS`
and `STARROCKS_BR_DEFAULT_BACKEND` identically. A future backend (e.g. "kafka", "redis") is
registered in `KNOWN_BACKEND_FACTORIES` - no other module changes.
"""

import os

from .backend import BackendRegistry
from .thread_backend import ThreadBackend

KNOWN_BACKEND_FACTORIES = {
    "thread": ThreadBackend,
}


class UnimplementedBackendError(ValueError):
    pass


def get_enabled_backends() -> list[str]:
    raw = os.getenv("STARROCKS_BR_ENABLED_BACKENDS", "thread")
    return [name.strip() for name in raw.split(",") if name.strip()]


def get_default_backend() -> str:
    return os.getenv("STARROCKS_BR_DEFAULT_BACKEND", "thread")


def build_backend_registry() -> BackendRegistry:
    enabled = get_enabled_backends()
    unknown = [name for name in enabled if name not in KNOWN_BACKEND_FACTORIES]
    if unknown:
        raise UnimplementedBackendError(
            f"STARROCKS_BR_ENABLED_BACKENDS lists unimplemented backend(s): {', '.join(unknown)}. "
            f"Available: {', '.join(KNOWN_BACKEND_FACTORIES)}"
        )
    backends = {name: KNOWN_BACKEND_FACTORIES[name]() for name in enabled}
    return BackendRegistry(backends, get_default_backend())
