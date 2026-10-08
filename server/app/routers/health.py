"""Health endpoints, shaped for Uptime Kuma (architecture.md §13).

Status codes do the work so each monitor needs no JSON query, and the split
between /health and /health/ready is deliberate: "the app is up" and "its
dependencies are healthy" must stay separately diagnosable, or a database
problem and a crashed process look identical from outside.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Response, status

from .. import db
from ..config import Settings, get_settings

router = APIRouter(tags=["health"])

STARTED_AT = time.monotonic()


def require_monitor_token(
    token: str = "",
    settings: Settings = Depends(get_settings),
) -> None:
    """A dedicated read-only token — not the admin session, and not behind TOTP.

    Uptime Kuma cannot do TOTP, so reusing admin credentials here would mean
    either weakening them or not monitoring at all.
    """
    if not settings.monitoring_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="monitoring disabled (no monitor_token configured)",
        )
    # Constant-time comparison: this endpoint is deliberately exempt from the
    # fail2ban ladder, so it must not leak the token through timing instead.
    import hmac

    if not hmac.compare_digest(token, settings.monitor_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="bad token")


@router.get("/health", summary="Liveness")
async def health() -> dict:
    """Deliberately does not touch the database.

    That is the whole point of having both this and /health/ready: if this
    answers and /health/ready does not, the fault is storage, not the process.
    """
    return {"status": "ok", "uptime_s": round(time.monotonic() - STARTED_AT, 1)}


@router.get("/health/ready", summary="Readiness")
async def ready(response: Response) -> dict:
    checks = {"database": await db.check_connection()}
    ok = all(checks.values())
    response.status_code = status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ok else "degraded", "checks": checks}


@router.get("/health/detail", summary="Full detail", dependencies=[Depends(require_monitor_token)])
async def detail(settings: Settings = Depends(get_settings)) -> dict:
    """Token-gated, because it describes internals.

    Devices, WireGuard peers and delivery backlogs join this as their models
    land; for now it reports what exists.
    """
    database = await db.check_connection()
    return {
        "status": "ok" if database else "degraded",
        "uptime_s": round(time.monotonic() - STARTED_AT, 1),
        "protocol": {
            "version": settings.protocol_version,
            "minimum": settings.min_protocol_version,
        },
        "checks": {"database": {"ok": database}},
        "media": {
            "browser_voip_mode": settings.browser_voip_mode,
            "wg_subnet": settings.wg_subnet,
        },
    }
