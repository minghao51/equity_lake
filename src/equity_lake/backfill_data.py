#!/usr/bin/env python3
"""
Backfill Historical Data

Fetches historical EOD data for a configurable date range using bulk
downloads instead of day-by-day API calls.

- US / HK / SG markets use yfinance date-range downloads
- CN A-shares use akshare date-range queries

Deduplication: write_to_partitioned_parquet already skips existing
(ticker, date) rows in each date partition, so re-running is safe.

Usage:
    uv run equity-backfill --start 2023-04-06 --end 2026-04-05
    uv run equity-backfill --days-back 1095
    uv run equity-backfill --days-back 365 --markets us
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import structlog
import yfinance as yf

from equity_lake.config import TickerConfig
from equity_lake.config.settings import get_settings
from equity_lake.core.runtime import (
    STANDARD_COLUMNS,
    setup_logging,
)
from equity_lake.ingestion.writers import write_to_partitioned_parquet

logger = structlog.get_logger()
SETTINGS = get_settings()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill historical equity data")
    parser.add_argument("--start", type=str, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, help="End date (YYYY-MM-DD, default: yesterday)")
    parser.add_argument("--days-back", type=int, help="Calendar days back from today")
    parser.add_argument("--markets", type=str, default="us,cn,hk_sg", help="Comma-separated markets")
    parser.add_argument("--dry-run", action="store_true", help="No writes")
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# US / HK_SG  (yfinance bulk download)
# ---------------------------------------------------------------------------


def backfill_yfinance(
    tickers: list[str],
    market_dir_name: str,
    start_date: date,
    end_date: date,
    dry_run: bool,
) -> int:
    """Download a date range via yfinance and write per-date Parquet partitions."""
    market_label = market_dir_name.replace("_", " ").upper()
    logger.info(f"{market_label}: downloading {len(tickers)} tickers, {start_date} to {end_date}")

    data = yf.download(
        tickers,
        start=start_date.isoformat(),
        end=(end_date + timedelta(days=1)).isoformat(),
        group_by="ticker",
        progress=True,
        auto_adjust=False,
        threads=True,
    )

    if data.empty:
        logger.warning(f"{market_label}: no data returned")
        return 0

    # Normalise multi-ticker vs single-ticker output
    frames: list[pd.DataFrame] = []
    if len(tickers) == 1:
        t_df = data.copy()
        t_df.columns = [c if isinstance(c, str) else str(c) for c in t_df.columns]
        t_df["ticker"] = tickers[0]
        t_df["date"] = t_df.index.date
        frames.append(t_df)
    else:
        for ticker in tickers:
            if ticker not in data.columns.get_level_values(0):
                continue
            t_df = data[ticker].copy()
            t_df["ticker"] = ticker
            t_df["date"] = t_df.index.date
            frames.append(t_df)

    if not frames:
        logger.warning(f"{market_label}: no valid ticker data")
        return 0
    df = pd.concat(frames)

    # Standardise column names
    col_map = {"Adj Close": "adj_close", "Close": "close", "Open": "open", "High": "high", "Low": "low", "Volume": "volume"}
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    if "adj_close" not in df.columns:
        df["adj_close"] = df["close"]

    cols = [c for c in STANDARD_COLUMNS if c in df.columns]
    df = df[cols].dropna(subset=["close"])

    total_rows = len(df)
    unique_dates = df["date"].nunique()
    logger.info(f"{market_label}: {total_rows} rows across {unique_dates} dates")

    if dry_run:
        logger.info(f"[DRY RUN] Would write {unique_dates} date partitions to {market_dir_name}")
        return total_rows

    # Write per-date partitions
    written = 0
    for trading_date, group in df.groupby("date"):
        td = trading_date if isinstance(trading_date, date) else pd.Timestamp(trading_date).date()
        day_df = group.copy()
        day_df["date"] = pd.Timestamp(td)
        ok = write_to_partitioned_parquet(day_df, market_dir_name, td, dry_run=False)
        if ok:
            written += len(day_df)

    logger.info(f"{market_label}: wrote {written}/{total_rows} rows")
    return written


# ---------------------------------------------------------------------------
# China A-shares  (akshare bulk download)
# ---------------------------------------------------------------------------


def backfill_cn(
    tickers: list[str],
    start_date: date,
    end_date: date,
    dry_run: bool,
) -> int:
    """Fetch CN A-share history via akshare and write per-date Parquet."""
    import akshare as ak

    logger.info(f"CN A-shares: downloading {len(tickers)} tickers, {start_date} to {end_date}")
    start_str = start_date.strftime("%Y%m%d")
    end_str = end_date.strftime("%Y%m%d")

    all_frames: list[pd.DataFrame] = []
    failed_tickers: list[str] = []

    def fetch_one(stock_code: str) -> pd.DataFrame | None:
        try:
            df = ak.stock_zh_a_hist(
                symbol=stock_code,
                period="daily",
                start_date=start_str,
                end_date=end_str,
                adjust="",
            )
            if df is None or df.empty:
                failed_tickers.append(stock_code)
                return None
            df["ticker"] = stock_code
            return df
        except Exception as exc:
            logger.warning(f"CN {stock_code} failed: {exc}")
            failed_tickers.append(stock_code)
            return None

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_one, t): t for t in tickers}
        for future in as_completed(futures):
            result = future.result(timeout=120)
            if result is not None:
                all_frames.append(result)

    if not all_frames:
        logger.warning("CN A-shares: primary source returned no data, trying yfinance fallback")

    # Fallback for tickers not returned by akshare
    fallback_tickers = list(dict.fromkeys(failed_tickers))
    if fallback_tickers:
        fallback_frames = _fetch_cn_from_yfinance(
            fallback_tickers,
            start_date,
            end_date,
        )
        if fallback_frames:
            all_frames.extend(fallback_frames)

    if not all_frames:
        logger.warning("CN A-shares: no data fetched from primary or fallback")
        return 0

    df = pd.concat(all_frames, ignore_index=True)
    df = df.rename(
        columns={
            "日期": "date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
        }
    )
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"]).dt.date
    if "adj_close" not in df.columns:
        df["adj_close"] = df["close"]

    cols = [c for c in STANDARD_COLUMNS if c in df.columns]
    df = df[cols].dropna(subset=["close"])

    total_rows = len(df)
    logger.info(f"CN A-shares: {total_rows} rows, {df['ticker'].nunique()} tickers")

    if dry_run:
        logger.info(f"[DRY RUN] Would write {df['date'].nunique()} date partitions to cn_ashare")
        return total_rows

    written = 0
    for trading_date, group in df.groupby("date"):
        td = trading_date if isinstance(trading_date, date) else trading_date.date()
        day_df = group.copy()
        day_df["date"] = pd.Timestamp(td)
        ok = write_to_partitioned_parquet(day_df, "cn_ashare", td, dry_run=False)
        if ok:
            written += len(day_df)

    logger.info(f"CN A-shares: wrote {written}/{total_rows} rows")
    return written


def _cn_to_yahoo_symbol(code: str) -> str:
    """Map 6-digit CN ticker to Yahoo Finance symbol."""
    if code.startswith(("0", "2", "3")):
        return f"{code}.SZ"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return f"{code}.SS"


def _fetch_cn_from_yfinance(
    tickers: list[str],
    start_date: date,
    end_date: date,
) -> list[pd.DataFrame]:
    """Fallback CN fetch from Yahoo Finance using exchange suffixes."""
    yf_symbols = [_cn_to_yahoo_symbol(t) for t in tickers]
    symbol_to_code = dict(zip(yf_symbols, tickers, strict=False))
    logger.info(f"CN fallback (yfinance): downloading {len(yf_symbols)} tickers")

    data = yf.download(
        yf_symbols,
        start=start_date.isoformat(),
        end=(end_date + timedelta(days=1)).isoformat(),
        group_by="ticker",
        progress=False,
        auto_adjust=False,
        threads=True,
    )
    if data.empty:
        logger.warning("CN fallback (yfinance): no data returned")
        return []

    frames: list[pd.DataFrame] = []
    if len(yf_symbols) == 1:
        symbol = yf_symbols[0]
        code = symbol_to_code[symbol]
        t_df = data.copy()
        t_df["ticker"] = code
        t_df["date"] = t_df.index.date
        frames.append(t_df)
    else:
        present_symbols = set(data.columns.get_level_values(0))
        for symbol in yf_symbols:
            if symbol not in present_symbols:
                continue
            t_df = data[symbol].copy()
            t_df["ticker"] = symbol_to_code[symbol]
            t_df["date"] = t_df.index.date
            frames.append(t_df)

    if not frames:
        logger.warning("CN fallback (yfinance): no valid ticker data")
        return []

    normalized: list[pd.DataFrame] = []
    col_map = {
        "Adj Close": "adj_close",
        "Close": "close",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Volume": "volume",
    }
    for frame in frames:
        df = frame.rename(columns=col_map)
        if "adj_close" not in df.columns:
            df["adj_close"] = df["close"]
        normalized.append(df)

    logger.info(f"CN fallback (yfinance): recovered {len(normalized)} tickers")
    return normalized


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    args = parse_args()
    log_level = "DEBUG" if args.verbose else "INFO"
    setup_logging("backfill", level=log_level, log_file="backfill.log")

    # Resolve date range
    yesterday = date.today() - timedelta(days=1)
    end_date = date.fromisoformat(args.end) if args.end else yesterday
    if args.days_back:
        start_date = end_date - timedelta(days=args.days_back)
    elif args.start:
        start_date = date.fromisoformat(args.start)
    else:
        logger.error("Must specify --days-back or --start")
        sys.exit(1)

    logger.info(f"Backfill range: {start_date} to {end_date}")

    # Load tickers
    config_path = Path(SETTINGS.ingestion.ticker_config_path)
    ticker_config = TickerConfig(config_path=config_path)

    markets = [m.strip() for m in args.markets.split(",")]
    total = 0

    for market in markets:
        if market == "us":
            tickers = ticker_config.get_tickers_for_market("us", active_only=True)
            total += backfill_yfinance(tickers, "us_equity", start_date, end_date, args.dry_run)
        elif market == "hk_sg":
            hk = ticker_config.get_tickers_for_market("hk", active_only=True)
            sg = ticker_config.get_tickers_for_market("sg", active_only=True)
            total += backfill_yfinance(hk + sg, "hk_sg_equity", start_date, end_date, args.dry_run)
        elif market == "cn":
            tickers = ticker_config.get_tickers_for_market("cn", active_only=True)
            total += backfill_cn(tickers, start_date, end_date, args.dry_run)
        else:
            logger.error(f"Unknown market: {market}")

    logger.info(f"Backfill complete: {total} total rows across {markets}")


if __name__ == "__main__":
    main()
