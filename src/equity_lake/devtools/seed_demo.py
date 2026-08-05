"""Populate the canonical lake with a demo US universe for the Strategy Lab.

Offline-safe by design: generates deterministic synthetic OHLCV by default, so the
whole showcase pipeline (arena -> FindingCards) runs with **no network or API
keys**. With ``real=True`` it attempts a live ``yfinance`` history pull and falls
back to synthetic on any failure.

Writes to the canonical medallion bronze path (``01_bronze/market_data/us_equity``)
so ``equity arena run`` and the production pipeline read the same data. Idempotent
via ``mode="overwrite"``.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import polars as pl
import structlog

logger = structlog.get_logger(__name__)

# Built-in demo universe — every symbol is defined & active in
# config/tickers.yaml (markets.us), so the `demo` config group resolves to these.
DEMO_UNIVERSE: list[str] = [
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "META",
    "NVDA",
    "TSLA",
    "BRK-B",
    "JPM",
    "V",
    "MA",
    "BAC",
    "WFC",
    "UNH",
    "JNJ",
    "LLY",
    "TMO",
    "MRK",
    "ABT",
    "AVGO",
    "WMT",
    "PG",
    "KO",
    "PEP",
    "COST",
    "HD",
    "MCD",
    "NKE",
    "DIS",
    "NFLX",
    "XOM",
    "CVX",
    "COP",
    "CAT",
    "BA",
    "GE",
    "HON",
    "UNP",
    "ADBE",
    "CRM",
    "ORCL",
    "AMD",
    "INTC",
    "CSCO",
    "QCOM",
    "IBM",
    "VZ",
    "CMCSA",
    "DHR",
    "LIN",
]

US_EQUITY_MARKET = "01_bronze/market_data/us_equity"


def _trading_days(years: float) -> list[date]:
    """Business days (Mon-Fri) ending yesterday, spanning ~`years` years."""
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=int(years * 365))
    days: list[date] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    return days


def _synthetic_frame(tickers: list[str], days: list[date], seed: int) -> pl.DataFrame:
    """Deterministic long OHLCV frame (geometric random walk per ticker)."""
    rng = np.random.default_rng(seed)
    frames: list[pl.DataFrame] = []
    for t in tickers:
        start_px = rng.uniform(50.0, 500.0)
        rets = rng.normal(0.0002, 0.015, len(days))
        close = start_px * np.exp(np.cumsum(rets))
        open_ = np.roll(close, 1)
        open_[0] = start_px
        span = np.abs(rng.normal(0.0, 0.008, len(days)))
        high = np.maximum(open_, close) * (1 + span)
        low = np.minimum(open_, close) * (1 - span)
        vol = rng.lognormal(16.0, 0.5, len(days)).astype(np.int64)
        frames.append(
            pl.DataFrame(
                {
                    "ticker": t,
                    "date": days,
                    "open": np.round(open_, 2),
                    "high": np.round(high, 2),
                    "low": np.round(low, 2),
                    "close": np.round(close, 2),
                    "volume": vol,
                }
            )
        )
    return pl.concat(frames)


def _try_real_fetch(tickers: list[str], years: float) -> pl.DataFrame | None:
    """Attempt a live yfinance history pull; return None on any failure."""
    try:
        import yfinance as yf
    except Exception as exc:  # noqa: BLE001
        logger.warning("seed_yfinance_unavailable", error=str(exc))
        return None

    frames: list[pl.DataFrame] = []
    for t in tickers:
        try:
            hist = yf.Ticker(t).history(period=f"{int(round(years))}y", auto_adjust=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("seed_ticker_failed", ticker=t, error=str(exc))
            continue
        if hist is None or hist.empty:
            continue
        frames.append(
            pl.DataFrame(
                {
                    "ticker": t,
                    "date": [idx.date() for idx in hist.index],
                    "open": hist["Open"].to_numpy(),
                    "high": hist["High"].to_numpy(),
                    "low": hist["Low"].to_numpy(),
                    "close": hist["Close"].to_numpy(),
                    "volume": hist["Volume"].to_numpy(),
                }
            )
        )
    if not frames:
        logger.warning("seed_real_fetch_empty")
        return None
    return pl.concat(frames).drop_nulls(subset=["close"])


def resolve_universe(tickers: list[str] | None) -> list[str]:
    """Resolve the demo universe: explicit > config `demo` group > built-in default."""
    if tickers:
        return [t.strip().upper() for t in tickers if t.strip()]
    try:
        from equity_lake.core.config import TickerConfig

        group = TickerConfig().get_tickers_by_group("demo", active_only=True)
        if group:
            return group
    except Exception as exc:  # noqa: BLE001
        logger.debug("seed_demo_group_unavailable", error=str(exc))
    return list(DEMO_UNIVERSE)


def seed_demo(
    *,
    years: float = 5.0,
    tickers: list[str] | None = None,
    real: bool = False,
    seed: int = 42,
    verbose: bool = False,
    lake_dir: Any | None = None,
) -> dict[str, Any]:
    """Seed the canonical lake with a demo US universe.

    Args:
        years: Years of history to generate/fetch.
        tickers: Explicit ticker list; ``None`` resolves the `demo` config group
            (falling back to :data:`DEMO_UNIVERSE`).
        real: Attempt a live yfinance pull; fall back to synthetic on failure.
        seed: Deterministic seed for synthetic generation.
        verbose: Debug logging.
        lake_dir: Override lake root (default ``LAKE_DIR``).

    Returns:
        Summary dict: ``{tickers, rows, days, source, path}``.
    """
    from equity_lake.core.logging import setup_structured_logging
    from equity_lake.core.paths import LAKE_DIR
    from equity_lake.storage.delta import write_delta

    setup_structured_logging(level="DEBUG" if verbose else "INFO")

    universe = resolve_universe(tickers)
    if not universe:
        raise ValueError("No tickers resolved for demo seed.")

    source = "synthetic"
    df: pl.DataFrame | None = None
    if real:
        logger.info("seed_demo_real_fetch", tickers=len(universe), years=years)
        df = _try_real_fetch(universe, years)
        if df is not None:
            source = "real"

    if df is None:
        days = _trading_days(years)
        df = _synthetic_frame(universe, days, seed)
        logger.info("seed_demo_synthetic", tickers=len(universe), days=len(days))

    if df.is_empty():
        raise ValueError("Seed produced no rows.")

    target_root = lake_dir or LAKE_DIR
    write_delta(df, US_EQUITY_MARKET, mode="overwrite", lake_dir=target_root)

    summary: dict[str, Any] = {
        "tickers": df["ticker"].n_unique(),
        "rows": df.height,
        "days": df["date"].n_unique(),
        "source": source,
        "path": str(target_root),
    }
    logger.info("seed_demo_complete", **summary)
    return summary


__all__ = ["DEMO_UNIVERSE", "resolve_universe", "seed_demo"]
