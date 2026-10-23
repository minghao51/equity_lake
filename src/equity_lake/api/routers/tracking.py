"""Loopback-only private tracking projection (ADR-0018)."""

from __future__ import annotations

import ipaddress
from importlib import import_module
from typing import Any, cast
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request


def require_loopback(request: Request) -> None:
    """Deny private tracking over remote, proxied, or unparseable client connections."""
    client = request.client
    if client is None or any(request.headers.get(header) for header in _PROXY_HEADERS):
        raise HTTPException(status_code=403, detail="Private tracking is available only to direct loopback clients")
    try:
        if not ipaddress.ip_address(client.host).is_loopback:
            raise HTTPException(status_code=403, detail="Private tracking is available only to loopback clients")
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Private tracking client identity is not a loopback IP") from exc
    host_header = request.headers.get("host")
    parsed_host = urlsplit(f"//{host_header}").hostname if host_header else None
    if parsed_host is None:
        raise HTTPException(status_code=403, detail="Private tracking requires an explicit loopback Host")
    host = parsed_host.lower()
    if host != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise HTTPException(status_code=403, detail="Private tracking Host must be localhost or a loopback IP")
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="Private tracking Host is not loopback") from exc


router = APIRouter(prefix="/tracking/private", tags=["private-tracking"], dependencies=[Depends(require_loopback)])
_PROXY_HEADERS = (
    "forwarded",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-port",
    "x-forwarded-prefix",
    "x-forwarded-proto",
    "x-real-ip",
    "via",
    "proxy-connection",
)


def _snapshot() -> Any:
    return import_module("equity_lake.signals.tracking").private_tracking_snapshot()


@router.get("")
def private_tracking_view(request: Request) -> dict[str, Any]:
    """Return the local watchlist, notes, holdings, readiness, history, conflicts, and delivery state."""
    del request
    snapshot: Any = _snapshot()
    return cast(dict[str, Any], snapshot.model_dump(mode="json"))


@router.get("/watchlist")
def private_watchlist(request: Request) -> dict[str, Any]:
    """Return the explicitly private local watchlist; no static/public projection uses this route."""
    del request
    snapshot: Any = _snapshot()
    return cast(dict[str, Any], snapshot.watchlist.model_dump(mode="json"))


@router.get("/readiness")
def private_readiness(request: Request) -> dict[str, Any]:
    """Return exact-session readiness and stale-run warnings."""
    del request
    snapshot: Any = _snapshot()
    return {
        "generated_at": snapshot.generated_at.isoformat(),
        "readiness": [item.model_dump(mode="json") for item in snapshot.readiness],
        "last_successful_scan_at": snapshot.last_successful_scan_at.isoformat() if snapshot.last_successful_scan_at else None,
        "stale_run_warning": snapshot.stale_run_warning,
        "history_status": snapshot.history_status,
    }


@router.get("/inbox")
def private_inbox(request: Request) -> list[dict[str, Any]]:
    """Return candidate events and outbox states without inventing delivery success."""
    del request
    snapshot: Any = _snapshot()
    return [cast(dict[str, Any], item.model_dump(mode="json")) for item in snapshot.recent_events]


@router.get("/history")
def private_history(request: Request) -> dict[str, Any]:
    """Return bounded persisted canonical scan reports with typed per-rule outcomes."""
    del request
    snapshot: Any = _snapshot()
    return {"status": snapshot.history_status, "reports": [report.model_dump(mode="json") for report in snapshot.recent_reports]}
