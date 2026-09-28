
"""Server-level configuration read from the environment at startup."""

import os

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


def get_enabled_backends() -> list[str]:
    raw = os.getenv("STARROCKS_BR_ENABLED_BACKENDS", "thread")
    return [name.strip() for name in raw.split(",") if name.strip()]


def get_default_backend() -> str:
    return os.getenv("STARROCKS_BR_DEFAULT_BACKEND", "thread")
