"""Predictions read endpoints (Phase 2B)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from equity_lake.api import deps

router = APIRouter(prefix="/predictions", tags=["predictions"])


@router.get("")
def list_predictions(
    ticker: Annotated[str | None, Query(description="Filter to one ticker")] = None,
    limit: Annotated[int, Query(ge=1, le=1000, description="Max rows to return")] = 100,
) -> list[dict[str, Any]]:
    """List recent platinum predictions, newest-first, optionally per ticker."""
    return deps.list_predictions(ticker=ticker, limit=limit)
