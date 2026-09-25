# Copyright 2025 deep-bi
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""FastAPI application assembly.

Fails fast (raises before the app is constructed) when required server-side
secrets are missing, per specs/api-authentication "Server refuses to start
without a configured key". Importing this module is what CLIs like
`starrocks-br api serve` and test fixtures call into.
"""

from fastapi import FastAPI

from ..jobs.backend import BackendRegistry, set_registry
from ..jobs.thread_backend import ThreadBackend
from ..store.crypto import ENCRYPTION_KEY_ENV_VAR
from .config import API_KEY_ENV_VAR, get_api_key, get_default_backend, get_enabled_backends
from .routes import clusters, health, jobs, schedules

_KNOWN_BACKEND_FACTORIES = {
    "thread": ThreadBackend,
    # Future backends (e.g. "kafka", "redis") register their JobBackend
    # implementation here - no other code in this module changes.
}


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
    enabled = get_enabled_backends()
    unknown = [name for name in enabled if name not in _KNOWN_BACKEND_FACTORIES]
    if unknown:
        raise StartupConfigError(
            f"STARROCKS_BR_ENABLED_BACKENDS lists unimplemented backend(s): {', '.join(unknown)}. "
            f"Available: {', '.join(_KNOWN_BACKEND_FACTORIES)}"
        )
    backends = {name: _KNOWN_BACKEND_FACTORIES[name]() for name in enabled}
    return BackendRegistry(backends, get_default_backend())


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
    app.include_router(clusters.router)
    app.include_router(jobs.router)
    app.include_router(schedules.router)
    return app
