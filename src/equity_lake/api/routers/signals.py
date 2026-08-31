"""Signals read endpoints (Phase 2B)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from equity_lake.api import deps
from equity_lake.core.dates import resolve_trading_date
from equity_lake.storage.delta import DeltaReadError

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("")
def list_signals(
    target_date: Annotated[date | None, Query(description="Signal date (default: last completed trading day)")] = None,
) -> list[dict[str, Any]]:
    """List buy/sell/hold signals (defaults to the last completed trading day).

    The default is calendar-aware (``resolve_trading_date``), so weekends and
    market holidays resolve to the most recent session instead of returning an
    empty machine-local ``date.today()`` result.
    """
    resolved = target_date if target_date is not None else resolve_trading_date(None)
    try:
        return deps.list_signals(resolved)
    except DeltaReadError as exc:
        raise HTTPException(status_code=503, detail=f"Signal history is unreadable: {exc}") from exc
