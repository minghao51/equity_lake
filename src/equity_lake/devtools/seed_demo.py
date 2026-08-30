"""Populate a demo US universe lake for the Strategy Lab.

Offline-safe by design: generates deterministic synthetic OHLCV by default, so the
whole showcase pipeline (arena -> FindingCards) runs with **no network or API
keys**. With ``real=True`` it attempts a live ``yfinance`` history pull and falls
back to synthetic on any failure.

Safety rails: the default target is the auxiliary sample lake
(``data/sample/``), mirroring ``cli/bootstrap.py cmd_sample``. Writing to the
canonical lake (``LAKE_DIR``) requires an explicit ``lake_dir`` targeting it
**plus** ``overwrite_production_lake=True`` — the CLI additionally enforces an
interactive confirmation (or the ``--overwrite-production-lake`` flag) before
passing that authorization through. Idempotent via ``mode="overwrite"``.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
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


def _same_path(a: Path, b: Path) -> bool:
    """Path equality robust to case-insensitive filesystems (``DATA/lake`` vs ``data/lake``).

    When both paths exist, ``os.path.samefile`` compares the underlying store,
    so differently-cased spellings of the same directory compare equal. Otherwise
    fall back to a ``normcase`` string comparison (identity on case-sensitive
    platforms, case/slash-folded on case-insensitive ones).
    """
    if a.exists() and b.exists():
        try:
            return os.path.samefile(a, b)
        except OSError:
            pass
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _targets_production_lake(target_root: Path) -> bool:
    """True when the target root is the canonical lake or lives underneath it."""
    from equity_lake.core.paths import LAKE_DIR

    resolved = target_root.resolve()
    lake = LAKE_DIR.resolve()
    if _same_path(resolved, lake):
        return True
    return any(_same_path(lake, parent) for parent in resolved.parents)


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
    lake_dir: str | Path | None = None,
    overwrite_production_lake: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Seed a demo US universe into a lake.

    Args:
        years: Years of history to generate/fetch.
        tickers: Explicit ticker list; ``None`` resolves the `demo` config group
            (falling back to :data:`DEMO_UNIVERSE`).
        real: Attempt a live yfinance pull; fall back to synthetic on failure.
        seed: Deterministic seed for synthetic generation.
        verbose: Debug logging.
        lake_dir: Lake root override. Defaults to the auxiliary sample lake
            (``data/sample/``); targeting the canonical ``LAKE_DIR`` (or a path
            under it) requires ``overwrite_production_lake=True``.
        overwrite_production_lake: Explicit authorization to overwrite the
            canonical lake. Without it, a production-lake target raises
            :class:`ValueError` instead of writing.
        dry_run: Preview the seed summary without writing anything.

    Returns:
        Summary dict: ``{tickers, rows, days, source, path, dry_run}``.
    """
    from equity_lake.core.logging import setup_structured_logging
    from equity_lake.core.paths import DATA_DIR
    from equity_lake.storage.delta import write_delta

    setup_structured_logging(level="DEBUG" if verbose else "INFO")

    universe = resolve_universe(tickers)
    if not universe:
        raise ValueError("No tickers resolved for demo seed.")

    target_root = Path(lake_dir) if lake_dir is not None else DATA_DIR / "sample"
    production_target = _targets_production_lake(target_root)
    if production_target and not overwrite_production_lake:
        raise ValueError(
            f"Refusing to overwrite the canonical lake at {target_root} without an explicit override. "
            f"Seed the default sample lake ({DATA_DIR / 'sample'}) instead, or pass --lake together with "
            "--overwrite-production-lake (or confirm interactively) to authorize the bronze overwrite."
        )

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

    if dry_run:
        summary: dict[str, Any] = {
            "tickers": df["ticker"].n_unique(),
            "rows": df.height,
            "days": df["date"].n_unique(),
            "source": source,
            "path": str(target_root),
            "dry_run": True,
        }
        logger.info("seed_demo_dry_run", **summary)
        return summary

    if production_target:
        logger.warning(
            "seed_demo_overwrite_production_lake",
            path=str(target_root),
            table=US_EQUITY_MARKET,
            mode="overwrite",
        )

    write_delta(df, US_EQUITY_MARKET, mode="overwrite", lake_dir=target_root)

    summary = {
        "tickers": df["ticker"].n_unique(),
        "rows": df.height,
        "days": df["date"].n_unique(),
        "source": source,
        "path": str(target_root),
        "dry_run": False,
    }
    logger.info("seed_demo_complete", **summary)
    return summary


__all__ = ["DEMO_UNIVERSE", "resolve_universe", "seed_demo"]
