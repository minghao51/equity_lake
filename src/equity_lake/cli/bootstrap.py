"""Bootstrap commands for Equity Lake.

Provides sample data generation for quick testing and onboarding.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from equity_lake.core.logging import setup_logging
from equity_lake.core.paths import CN_ASHARE_DIR, HK_SG_EQUITY_DIR, US_EQUITY_DIR

logger = setup_logging(__name__)

# US ticker format regex (from validators.py)
US_TICKER_PATTERN = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,2})?$")

# ---------------------------------------------------------------------------
# Curated sample tickers (small subset from each market)
# ---------------------------------------------------------------------------

SAMPLE_TICKERS = {
    "us_equity": ["AAPL", "MSFT", "GOOGL", "NVDA", "JPM"],
    "cn_ashare": ["600519", "000001", "601318", "601398", "000858"],
    "hk_sg_equity": ["0700.HK", "9988.HK", "D05.SI", "0005.HK", "O39.SI"],
}

MARKET_DIRS = {
    "us_equity": US_EQUITY_DIR,
    "cn_ashare": CN_ASHARE_DIR,
    "hk_sg_equity": HK_SG_EQUITY_DIR,
}


# ---------------------------------------------------------------------------
# Real data extraction (from existing lake)
# ---------------------------------------------------------------------------


def _try_load_real_data(
    ticker: str,
    market: str,
    start_date: date,
    end_date: date,
) -> pd.DataFrame | None:
    """Try to load real data from the lake for a ticker/date range."""
    lake_dir = MARKET_DIRS.get(market)
    if lake_dir is None or not lake_dir.exists():
        return None

    try:
        import duckdb

        conn = duckdb.connect(":memory:")
        query = f"""
            SELECT *
            FROM read_parquet('{lake_dir}/**/*.parquet', hive_partitioning=1)
            WHERE ticker = ?
              AND date >= ?
              AND date <= ?
            ORDER BY date
        """
        result = conn.execute(query, [ticker, str(start_date), str(end_date)]).fetchdf()
        conn.close()
        if result.empty:
            return None
        return result
    except Exception:
        return None


def _load_sample_from_lake(
    days: int,
    tickers_override: dict[str, list[str]] | None = None,
) -> tuple[pd.DataFrame, dict[str, list[str]], bool]:
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
            if real_data is not None and not real_data.empty:
                # Limit to the requested number of trading days
                trading_days = real_data["date"].nunique()
                if trading_days > days:
                    unique_dates = sorted(real_data["date"].unique())[-days:]
                    real_data = real_data[real_data["date"].isin(unique_dates)]
                frames.append(real_data)
                logger.info("Loaded real data for %s (%s rows)", ticker, len(real_data))
            else:
                logger.debug("No real data for %s, will generate synthetic", ticker)

    if frames:
        combined = pd.concat(frames, ignore_index=True)
        return combined, tickers, True

    return pd.DataFrame(), tickers, False


# ---------------------------------------------------------------------------
# Synthetic data generation (fallback)
# ---------------------------------------------------------------------------


class _SyntheticGenerator:
    """Generate realistic OHLCV data when real data is unavailable."""

    def __init__(self, seed: int = 42):
        self.seed = seed
        np.random.seed(seed)

    def generate_ticker(
        self,
        ticker: str,
        dates: list[date],
        price_range: tuple[float, float] = (10, 500),
        volume_range: tuple[int, int] = (1_000_000, 50_000_000),
    ) -> pd.DataFrame:
        num_days = len(dates)
        start_price = np.random.uniform(*price_range)

        # Geometric Brownian Motion for prices
        returns = np.random.normal(0.0001, 0.02, num_days)
        prices = start_price * np.exp(np.cumsum(returns))

        # OHLC from close prices
        closes = np.maximum(prices, 0.01)
        opens = np.roll(closes, 1)
        opens[0] = start_price
        daily_range = np.abs(np.random.normal(0, 0.01, num_days))
        highs = np.maximum.reduce([opens, closes]) * (1 + daily_range)
        lows = np.minimum.reduce([opens, closes]) * (1 - daily_range)
        lows = np.maximum(lows, 0.01)

        # Volume
        base_vol = np.random.uniform(*volume_range)
        volumes = np.random.lognormal(np.log(base_vol), 0.5, num_days).astype(np.int64)
        volumes = np.clip(volumes, *volume_range)

        df = pd.DataFrame(
            {
                "ticker": ticker,
                "date": dates,
                "open": np.round(opens, 2),
                "high": np.round(highs, 2),
                "low": np.round(lows, 2),
                "close": np.round(closes, 2),
                "volume": volumes,
                "adj_close": np.round(closes, 2),
            }
        )
        return df


# ---------------------------------------------------------------------------
# Trading date generation
# ---------------------------------------------------------------------------


def _trading_dates(start: date, end: date) -> list[date]:
    """Generate trading dates (Mon-Fri only)."""
    dates = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return dates


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
    from equity_lake.core.paths import DATA_DIR

    log_level = "DEBUG" if verbose else "INFO"
    setup_logging(__name__, level=log_level)

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

    if not combined_data.empty and used_real:
        logger.info("✅ Loaded real data from lake (%s rows)", len(combined_data))
    else:
        logger.info("No real data found in lake — generating synthetic data")
        generator = _SyntheticGenerator(seed=seed)

        end_date = date.today() - timedelta(days=1)
        start_date = end_date - timedelta(days=days * 2)
        trading = _trading_dates(start_date, end_date)
        # Limit to exactly `days` trading days
        trading = trading[-days:]

        price_configs = {
            "us_equity": (50, 500),
            "cn_ashare": (5, 200),
            "hk_sg_equity": (5, 300),
        }
        volume_configs = {
            "us_equity": (2_000_000, 80_000_000),
            "cn_ashare": (5_000_000, 100_000_000),
            "hk_sg_equity": (1_000_000, 50_000_000),
        }

        frames = []
        for market, ticker_list in (ticker_override or tickers_used).items():
            p_range = price_configs.get(market, (10, 500))
            v_range = volume_configs.get(market, (1_000_000, 50_000_000))
            for t in ticker_list:
                df = generator.generate_ticker(t, trading, p_range, v_range)
                frames.append(df)
                logger.info("Generated synthetic data for %s (%s rows)", t, len(df))

        combined_data = pd.concat(frames, ignore_index=True)

    # Write hive-partitioned parquet
    logger.info("Writing hive-partitioned parquet to %s", out)

    for market, ticker_list in tickers_used.items():
        market_data = combined_data[combined_data["ticker"].isin(ticker_list)]
        if market_data.empty:
            continue

        market_dir = out / market
        market_dir.mkdir(parents=True, exist_ok=True)

        trading_days = sorted(market_data["date"].unique())
        for trading_date in trading_days:
            date_str = str(trading_date)
            partition_dir = market_dir / f"date={date_str}"
            partition_dir.mkdir(parents=True, exist_ok=True)
            parquet_path = partition_dir / f"{date_str}.parquet"

            if parquet_path.exists():
                continue

            day_data = market_data[market_data["date"] == trading_date].copy()
            day_data["date"] = pd.to_datetime(day_data["date"])
            day_data.to_parquet(parquet_path, index=False, compression="snappy")

    # Summary
    total_rows = len(combined_data)
    unique_tickers = combined_data["ticker"].nunique()
    unique_days = combined_data["date"].nunique()

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
    logger.info("  equity backtest --strategy sma_crossover --tickers AAPL,MSFT --start-date ... --end-date ...")
    logger.info("  equity query --sql 'SELECT * FROM read_parquet(\"%s/**/*.parquet\") LIMIT 10'", out)
