"""Predictions read endpoints (Phase 2B)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from equity_lake.api import deps
from equity_lake.storage.delta import DeltaReadError

router = APIRouter(prefix="/predictions", tags=["predictions"])


@router.get("")
def list_predictions(
    target_date: Annotated[date | None, Query(description="Only predictions for this date (partition-pruned read)")] = None,
    ticker: Annotated[str | None, Query(description="Filter to one ticker")] = None,
    limit: Annotated[int, Query(ge=1, le=1000, description="Max rows to return")] = 100,
) -> list[dict[str, Any]]:
    """List recent platinum predictions, newest-first, optionally per date/ticker."""
    try:
        return deps.list_predictions(target_date=target_date, ticker=ticker, limit=limit)
    except DeltaReadError as exc:
        raise HTTPException(status_code=503, detail=f"Predictions table is unreadable: {exc}") from exc
