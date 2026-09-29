"""FastAPI application assembly.

Fails fast (raises before the app is constructed) when required server-side
secrets are missing, per specs/api-authentication "Server refuses to start
without a configured key". Importing this module is what `uvicorn
starrocks_br.api.app:create_app --factory` and test fixtures call into.
"""

from fastapi import FastAPI

from ..jobs.backend import BackendRegistry, set_registry
from ..jobs.bootstrap import UnimplementedBackendError, build_backend_registry
from ..store.crypto import ENCRYPTION_KEY_ENV_VAR
from .config import API_KEY_ENV_VAR, get_api_key
from .routes import clusters, health, inventory_groups, jobs, repositories, schedules


class StartupConfigError(RuntimeError):
    pass


def _check_required_env() -> None:
    import os

    missing = []
    try:
        get_api_key()
    except Exception:
        missing.append(API_KEY_ENV_VAR)

    if not os.getenv(ENCRYPTION_KEY_ENV_VAR):
        missing.append(ENCRYPTION_KEY_ENV_VAR)

    if missing:
        raise StartupConfigError(
            "Cannot start API server, missing required environment variable(s): "
            + ", ".join(missing)
        )


def _build_backend_registry() -> BackendRegistry:
    try:
        return build_backend_registry()
    except UnimplementedBackendError as e:
        raise StartupConfigError(str(e)) from e


def create_app(*, check_env: bool = True) -> FastAPI:
    """Build the FastAPI app.

    `check_env=False` is used by test fixtures that set env vars themselves
    right before constructing the app but want to compose routers the same
    way production does.
    """
    if check_env:
        _check_required_env()

    set_registry(_build_backend_registry())

    app = FastAPI(title="starrocks-br API", version="1.0")
    app.include_router(health.router)
    app.include_router(clusters.cluster_router)
    app.include_router(clusters.clusters_router)
    app.include_router(jobs.router)
    app.include_router(schedules.router)
    app.include_router(repositories.router)
    app.include_router(inventory_groups.router)
    return app
