"""Signals read endpoints (Phase 2B)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from equity_lake.api import deps
from equity_lake.core.dates import resolve_trading_date
from equity_lake.storage.delta import DeltaReadError

router = APIRouter(prefix="/signals", tags=["signals"])

_PUBLIC_METADATA = {
    "strategy",
    "lookback_days",
    "pct_from_sma",
    "price",
    "sma",
    "sentiment_score",
    "article_count",
    "prediction",
    "probability",
    "horizon_days",
    "model_mode",
    "model_version",
    "execution_probability",
    "candidate_action",
    "candidate_source",
    "meta_label_threshold",
    "threshold_used",
    "threshold_source",
    "barrier_settings",
}


def _public_signal_projection(row: dict[str, Any]) -> dict[str, Any]:
    """Project only supported signal fields; never serialize arbitrary/private metadata."""
    metadata = row.get("metadata")
    safe_metadata = {key: value for key, value in metadata.items() if key in _PUBLIC_METADATA} if isinstance(metadata, dict) else {}
    market = row.get("market")
    mic = row.get("mic")
    identity_known = isinstance(market, str) and isinstance(mic, str) and row.get("identity_schema_version") == 1
    return {
        "ticker": row.get("ticker"),
        "date": row.get("date"),
        "signal_type": row.get("signal_type"),
        "action": row.get("action"),
        "confidence": row.get("confidence"),
        "reasoning": row.get("reasoning"),
        "market": market if identity_known else None,
        "mic": mic if identity_known else None,
        "identity_status": "canonical" if identity_known else "unknown_legacy",
        "metadata": safe_metadata,
    }


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
        return [_public_signal_projection(row) for row in deps.list_signals(resolved)]
    except DeltaReadError as exc:
        raise HTTPException(status_code=503, detail=f"Signal history is unreadable: {exc}") from exc
