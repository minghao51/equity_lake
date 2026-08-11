"""Backtest report read endpoints (Phase 2B)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from equity_lake.api import deps

router = APIRouter(prefix="/backtests", tags=["backtests"])


@router.get("")
def list_backtests() -> list[dict[str, Any]]:
    """List arena/backtest run reports (strategy x cost-regime metrics)."""
    return deps.list_backtests()
