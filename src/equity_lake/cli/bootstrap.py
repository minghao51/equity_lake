"""Bootstrap commands for Equity Lake.

Provides sample data generation for quick testing and onboarding.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import structlog

from equity_lake.core.paths import market_dir
from equity_lake.devtools.seeding import (
    SAMPLE_TICKERS,
    OhlcvProfile,
    synthetic_ohlcv,
    trailing_business_days,
)

logger = structlog.get_logger()

# US ticker format regex (from validators.py)
US_TICKER_PATTERN = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,2})?$")

# Curated sample tickers and the synthetic generator live in devtools/seeding.py
# (shared with ``equity demo seed``); SAMPLE_TICKERS is re-exported here because
# it defines the (three-market) bootstrap scope.
#
# Derived from the core/paths.py price-market registry (ADR-0010) — no private
# market->directory copy.
MARKET_DIRS = {market: market_dir(market) for market in SAMPLE_TICKERS}

# Per-market synthetic tuning for the sample lake (price/volume levels only —
# the random walk itself is shared).
SAMPLE_PROFILES: dict[str, OhlcvProfile] = {
    "us_equity": OhlcvProfile(price_range=(50, 500), volume_range=(2_000_000, 80_000_000)),
    "cn_ashare": OhlcvProfile(price_range=(5, 200), volume_range=(5_000_000, 100_000_000)),
    "hk_sg_equity": OhlcvProfile(price_range=(5, 300), volume_range=(1_000_000, 50_000_000)),
}
_DEFAULT_SAMPLE_PROFILE = OhlcvProfile(price_range=(10, 500), volume_range=(1_000_000, 50_000_000))


# ---------------------------------------------------------------------------
# Real data extraction (from existing lake)
# ---------------------------------------------------------------------------


def _try_load_real_data(
    ticker: str,
    market: str,
    start_date: date,
    end_date: date,
) -> pl.DataFrame | None:
    """Try to load real data from the lake for a ticker/date range."""
    lake_dir = MARKET_DIRS.get(market)
    if lake_dir is None or not lake_dir.exists():
        return None

    try:
        import duckdb

        from equity_lake.storage.lake_reader import duckdb_scan_for

        conn = duckdb.connect(":memory:")
        # duckdb_scan_for auto-detects Delta vs hive-parquet, matching the real lake layout.
        query = f"""
            SELECT *
            FROM {duckdb_scan_for(lake_dir)}
            WHERE ticker = ?
              AND date >= ?
              AND date <= ?
            ORDER BY date
        """
        result = conn.execute(query, [ticker, str(start_date), str(end_date)]).pl()
        conn.close()
        if result.is_empty():
            return None
        return result
    except Exception:
        return None


def _load_sample_from_lake(
    days: int,
    tickers_override: dict[str, list[str]] | None = None,
) -> tuple[pl.DataFrame, dict[str, list[str]], bool]:
    """Attempt to load sample data from the existing lake.

    Returns:
        (DataFrame, tickers_used, used_real_data)
    """
    tickers = tickers_override or SAMPLE_TICKERS
    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=days * 2)  # buffer for non-trading days

    frames = []

    for market, ticker_list in tickers.items():
        for ticker in ticker_list:
            real_data = _try_load_real_data(ticker, market, start_date, end_date)
            if real_data is not None and not real_data.is_empty():
                # Limit to the requested number of trading days
                trading_days = real_data["date"].n_unique()
                if trading_days > days:
                    unique_dates = real_data["date"].unique().sort().to_list()[-days:]
                    real_data = real_data.filter(pl.col("date").is_in(unique_dates))
                frames.append(real_data)
                logger.info("Loaded real data for %s (%s rows)", ticker, real_data.height)
            else:
                logger.debug("No real data for %s, will generate synthetic", ticker)

    if frames:
        combined = pl.concat(frames, how="diagonal_relaxed")
        return combined, tickers, True

    return pl.DataFrame(), tickers, False


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------


def cmd_sample(
    days: int = 30,
    tickers: str | None = None,
    output_dir: str | None = None,
    seed: int = 42,
    verbose: bool = False,
) -> None:
    """Generate sample data for quick testing.

    Tries to use real data from the lake first; falls back to synthetic
    generation if no lake data is available.

    Args:
        days: Number of trading days to generate
        tickers: Comma-separated ticker symbols (optional)
        output_dir: Output directory (default: data/sample/)
        seed: Random seed for synthetic generation
        verbose: Enable debug logging
    """
    from equity_lake.core.logging import setup_structured_logging
    from equity_lake.core.paths import DATA_DIR

    log_level = "DEBUG" if verbose else "INFO"
    setup_structured_logging(level=log_level)

    # Parse custom tickers if provided
    ticker_override: dict[str, list[str]] | None = None
    if tickers:
        # Assume all provided are US tickers for simplicity
        ticker_list = [t.strip().upper() for t in tickers.split(",")]
        # Validate US ticker format
        for t in ticker_list:
            if not US_TICKER_PATTERN.match(t):
                logger.error(
                    "Invalid US ticker format: %s. Expected format like AAPL, BRK-A",
                    t,
                )
                raise ValueError(f"Invalid ticker format: {t}")

        ticker_override = {"us_equity": ticker_list}
        logger.info("Using custom tickers for us_equity market: %s", ticker_override)

    # Output directory
    out = Path(output_dir) if output_dir else DATA_DIR / "sample"
    out.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 60)
    logger.info("Equity Lake — Sample Data Generator")
    logger.info("=" * 60)
    logger.info("Trading days requested: %s", days)
    logger.info("Output directory: %s", out)

    # Try to load from lake first
    logger.info("Checking existing lake for sample data...")
    combined_data, tickers_used, used_real = _load_sample_from_lake(days, ticker_override)

    if not combined_data.is_empty() and used_real:
        logger.info("✅ Loaded real data from lake (%s rows)", combined_data.height)
    else:
        logger.info("No real data found in lake — generating synthetic data")

        # Limit to exactly `days` trading days
        trading = trailing_business_days(days * 2)[-days:]

        # One RNG for the whole run so each market continues the same stream.
        rng = np.random.default_rng(seed)
        frames = []
        for market, ticker_list in (ticker_override or tickers_used).items():
            profile = SAMPLE_PROFILES.get(market, _DEFAULT_SAMPLE_PROFILE)
            for t in ticker_list:
                df = synthetic_ohlcv([t], trading, profile=profile, rng=rng)
                frames.append(df)
                logger.info("Generated synthetic data for %s (%s rows)", t, df.height)

        combined_data = pl.concat(frames)

    # Write Delta tables (one per market) via the canonical writer.
    from equity_lake.storage.delta import write_delta

    logger.info("Writing Delta tables to %s", out)

    for market, ticker_list in tickers_used.items():
        market_data = combined_data.filter(pl.col("ticker").is_in(ticker_list))
        if market_data.is_empty():
            continue
        write_delta(market_data, market, mode="overwrite", lake_dir=out)

    # Summary
    total_rows = combined_data.height
    unique_tickers = combined_data["ticker"].n_unique()
    unique_days = combined_data["date"].n_unique()

    logger.info("")
    logger.info("=" * 60)
    logger.info("Sample data generated successfully!")
    logger.info("=" * 60)
    logger.info("  Tickers:  %s", unique_tickers)
    logger.info("  Days:     %s", unique_days)
    logger.info("  Rows:     %s", total_rows)
    logger.info("  Location: %s", out)
    logger.info("")
    logger.info("Next steps:")
    logger.info("  equity signal scan --watchlist config/watchlist.yaml")
    logger.info("  equity backtest --strategy trend_following --tickers AAPL,MSFT --start-date ... --end-date ...")
    logger.info("  equity query --query latest_summary")
