"""Bearer-token authentication dependency, applied to every protected route.

Per specs/api-authentication: a single shared secret (STARROCKS_BR_API_KEY),
no user accounts, no sessions. The health endpoint is intentionally left
out of this dependency by not including it in any router that depends on
`require_api_key`.
"""

import hmac

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_api_key

_bearer_scheme = HTTPBearer(auto_error=False)


def require_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization bearer token",
        )

    expected = get_api_key()
    if not hmac.compare_digest(credentials.credentials, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )


__all__ = ["require_api_key"]
