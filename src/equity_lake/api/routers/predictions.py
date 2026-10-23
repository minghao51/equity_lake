"""Predictions read endpoint (bounded raw-history API)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response

from equity_lake.api import deps
from equity_lake.storage.delta import DeltaReadError

router = APIRouter(prefix="/predictions", tags=["predictions"])


def _prediction_coverage_headers(result: deps.PredictionReadResult) -> dict[str, str]:
    """Describe the bounded window and pinned snapshot without changing list JSON."""
    return {
        "X-Predictions-Window-Start": result.window_start.isoformat(),
        "X-Predictions-Window-End": result.window_end.isoformat(),
        "X-Predictions-Snapshot-Version": str(result.snapshot_version),
        "X-Predictions-Scan-Status": result.scan_status,
        "X-Predictions-Processed-Rows": str(result.processed_rows),
        "X-Predictions-Returned-Rows": str(len(result.rows)),
        "X-Predictions-Selected-Files": str(result.selected_files),
        "X-Predictions-Selected-File-Bytes": str(result.selected_file_bytes),
        "X-Predictions-Decoded-Bytes": str(result.decoded_bytes),
    }


def _budget_error_headers(exc: deps.PredictionScanBudgetExceededError) -> dict[str, str]:
    """Preserve coverage counters when a bounded query fails closed."""
    return {
        "X-Predictions-Window-Start": exc.window_start.isoformat(),
        "X-Predictions-Window-End": exc.window_end.isoformat(),
        "X-Predictions-Snapshot-Version": str(exc.snapshot_version),
        "X-Predictions-Scan-Status": "budget_exceeded",
        "X-Predictions-Processed-Rows": str(exc.processed_rows),
        "X-Predictions-Selected-Files": str(exc.selected_files),
        "X-Predictions-File-Bytes-Observed": str(exc.selected_file_bytes),
        "X-Predictions-Decoded-Bytes": str(exc.decoded_bytes),
    }


@router.get("")
def list_predictions(
    response: Response,
    target_date: Annotated[date | None, Query(description="One exact prediction date; explicit dates may access older history")] = None,
    ticker: Annotated[str | None, Query(min_length=1, description="Broad legacy alias; may match the same symbol across venues")] = None,
    market: Annotated[str | None, Query(min_length=1, description="Canonical market or supported short alias")] = None,
    mic: Annotated[str | None, Query(min_length=1, description="Explicit exchange MIC; never inferred from ticker")] = None,
    symbol: Annotated[str | None, Query(min_length=1, description="Exact canonical symbol filter")] = None,
    limit: Annotated[int, Query(ge=1, le=1000, description="Max historical rows to return")] = 100,
) -> list[dict[str, Any]]:
    """List raw historical predictions with per-row status and read coverage headers."""
    try:
        result = deps.list_predictions(target_date=target_date, ticker=ticker, market=market, mic=mic, symbol=symbol, limit=limit)
    except deps.PredictionQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except deps.PredictionScanBudgetExceededError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Predictions scan resource budget exceeded: {exc.reason}",
            headers=_budget_error_headers(exc),
        ) from exc
    except DeltaReadError as exc:
        raise HTTPException(status_code=503, detail=f"Predictions table is unreadable: {exc}") from exc

    response.headers.update(_prediction_coverage_headers(result))
    return result.rows
