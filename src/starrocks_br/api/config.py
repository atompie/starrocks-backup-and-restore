
"""Server-level configuration read from the environment at startup."""

import os

from ..jobs.bootstrap import get_default_backend, get_enabled_backends  # noqa: F401 - re-exported

API_KEY_ENV_VAR = "STARROCKS_BR_API_KEY"


class ApiKeyMissingError(RuntimeError):
    def __init__(self):
        super().__init__(
            f"{API_KEY_ENV_VAR} must be set before starting the API server. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )


def get_api_key() -> str:
    key = os.getenv(API_KEY_ENV_VAR)
    if not key:
        raise ApiKeyMissingError()
    return key
