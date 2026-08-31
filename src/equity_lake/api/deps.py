"""Thin dependency getters for the read API over the equity data lake.

Each getter wraps an existing reader (the findings writer, the signal history,
the Delta readers, or a filesystem listing) so routers stay thin and reuse the
canonical lake accessors — no duplicated I/O. FastAPI-free; routers call these
directly (patchable via ``monkeypatch.setattr(deps, ...)``).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from typing import Any, cast

import polars as pl

from equity_lake.core.paths import FINDINGS_DIR, MODELS_DIR
from equity_lake.findings.models import FindingCard
from equity_lake.findings.writer import load_finding_cards
from equity_lake.signals.history import load_signals
from equity_lake.storage.delta import DeltaReadError, delta_table_path


def list_findings() -> list[FindingCard]:
    """Load every serialized FindingCard under ``data/findings/``."""
    return load_finding_cards()


def list_signals(target_date: date) -> list[dict[str, Any]]:
    """Load buy/sell/hold signals for one date (empty list if none on disk)."""
    return [asdict(signal) for signal in load_signals(target_date)]


def list_models() -> list[dict[str, Any]]:
    """List trained-model summaries (one entry per ``*.training_summary.json``)."""
    summaries: list[dict[str, Any]] = []
    for path in sorted(MODELS_DIR.glob("*.training_summary.json")):
        try:
            summaries.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return summaries


def _read_predictions_frame(target_date: date | None) -> pl.DataFrame:
    """Read the predictions table, pruned to one date partition when requested.

    Predictions are partitioned by ``date``, so a date-scoped request reads only
    that partition instead of scanning the whole table (deltalake partition
    filter). Failures surface as :class:`DeltaReadError` exactly like
    :func:`equity_lake.storage.delta.read_delta` so routers map them to 503.
    """
    from deltalake import DeltaTable

    table = "04_platinum/predictions"
    try:
        dt = DeltaTable(str(delta_table_path(table)))
        if target_date is None:
            return cast(pl.DataFrame, pl.from_arrow(dt.to_pyarrow_table()))
        return cast(pl.DataFrame, pl.from_arrow(dt.to_pyarrow_table(partitions=[("date", "=", target_date.isoformat())])))
    except Exception as exc:
        raise DeltaReadError(table, exc) from exc


def list_predictions(*, target_date: date | None = None, ticker: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Read recent platinum predictions, newest-first, optionally filtered by date/ticker.

    When *target_date* is given the Delta read is partition-pruned to that date.
    """
    frame = _read_predictions_frame(target_date)
    if ticker is not None and "ticker" in frame.columns:
        frame = frame.filter(pl.col("ticker") == ticker)
    if "date" in frame.columns:
        frame = frame.sort("date", descending=True)
    return frame.head(limit).to_dicts()


def list_backtests() -> list[dict[str, Any]]:
    """List arena/backtest run reports (``data/findings/<slug>/metrics.json``)."""
    runs: list[dict[str, Any]] = []
    for metrics_path in sorted(FINDINGS_DIR.glob("*/metrics.json")):
        try:
            data: dict[str, Any] = json.loads(metrics_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        data["slug"] = metrics_path.parent.name
        runs.append(data)
    return runs


__all__ = [
    "list_backtests",
    "list_findings",
    "list_models",
    "list_predictions",
    "list_signals",
]
