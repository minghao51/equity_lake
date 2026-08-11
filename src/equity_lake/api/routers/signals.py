"""Signals read endpoints (Phase 2B)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query

from equity_lake.api import deps

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("")
def list_signals(
    target_date: Annotated[date | None, Query(description="Signal date (default: today)")] = None,
) -> list[dict[str, Any]]:
    """List buy/sell/hold signals for a date (defaults to today)."""
    return deps.list_signals(target_date or date.today())
