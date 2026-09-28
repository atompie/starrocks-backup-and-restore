"""Unauthenticated liveness endpoint - deliberately has no `require_api_key` dependency."""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
