from __future__ import annotations

from datetime import date
from typing import Any, Self, cast

import duckdb
import polars as pl
import structlog
from joblib import Memory

from equity_lake.core.paths import (
    CN_ASHARE_DIR,
    HK_SG_EQUITY_DIR,
    JPX_EQUITY_DIR,
    KRX_EQUITY_DIR,
    LOGS_DIR,
    US_EQUITY_DIR,
)

logger = structlog.get_logger(__name__)

CACHE_DIR = LOGS_DIR / "backtest_cache"
memory = Memory(CACHE_DIR, verbose=0)


class BacktestDataLoader:
    MARKET_DIRS = {
        "us": US_EQUITY_DIR,
        "cn": CN_ASHARE_DIR,
        "hk_sg": HK_SG_EQUITY_DIR,
        "jpx": JPX_EQUITY_DIR,
        "krx": KRX_EQUITY_DIR,
    }

    def __init__(
        self,
        db_path: str = ":memory:",
        cache_enabled: bool = True,
    ):
        self.db_path = db_path
        self.cache_enabled = cache_enabled
        self.conn = duckdb.connect(db_path)
        self._setup_views()

        logger.info(
            "BacktestDataLoader initialized",
            cache_enabled=cache_enabled,
            cache_dir=str(CACHE_DIR) if cache_enabled else None,
        )

    def _setup_views(self) -> None:
        from deltalake import DeltaTable

        logger.debug("Setting up market views...")
        self.conn.execute("INSTALL delta; LOAD delta;")

        for market_label, data_dir in self.MARKET_DIRS.items():
            if not data_dir.exists():
                logger.warning(
                    "Data directory not found, skipping view",
                    market=market_label,
                    path=str(data_dir),
                )
                continue

            view_name = f"backtest_{market_label}"

            if DeltaTable.is_deltatable(str(data_dir)):
                scan_from = f"delta_scan('{data_dir}')"
            else:
                scan_from = f"read_parquet('{data_dir / 'date=*/*.parquet'}', hive_partitioning=1)"

            sql = f"""
            CREATE OR REPLACE VIEW {view_name} AS
            SELECT
                ticker,
                date,
                open,
                high,
                low,
                close,
                volume,
                '{market_label}' as market
            FROM {scan_from}
            """

            try:
                self.conn.execute(sql)
                logger.debug("Created market view", market=market_label)
            except Exception as e:
                logger.error(
                    "Failed to create view",
                    market=market_label,
                    error=str(e),
                )

    def load(
        self,
        tickers: list[str],
        start_date: date,
        end_date: date,
        markets: list[str] | None = None,
        columns: list[str] | None = None,
        fill_method: str = "ffill",
    ) -> pl.DataFrame:
        if markets is None:
            markets = list(self.MARKET_DIRS.keys())

        if columns is None:
            columns = [
                "ticker",
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]

        logger.info(
            "Loading backtest data",
            tickers=len(tickers),
            start_date=str(start_date),
            end_date=str(end_date),
            markets=markets,
        )

        data = self._query_data(tickers, start_date, end_date, markets, columns)

        if data.is_empty():
            logger.warning("No data found for query", tickers=tickers)
            return pl.DataFrame()

        data = self._clean_data(data, fill_method)

        logger.debug(
            "Returned long format",
            shape=data.shape,
            tickers=data["ticker"].n_unique(),
        )

        return data

    def _query_data(
        self,
        tickers: list[str],
        start_date: date,
        end_date: date,
        markets: list[str],
        columns: list[str],
    ) -> pl.DataFrame:
        union_queries = []
        for market in markets:
            view_name = f"backtest_{market}"
            data_dir = self.MARKET_DIRS.get(market)

            if market in ["jpx", "krx"] and (not data_dir or not data_dir.exists()):
                logger.warning(
                    "Market data directory not found",
                    market=market,
                    path=str(data_dir) if data_dir else "None",
                    hint="Run equity ingest command first to fetch market data",
                )
                continue

            union_queries.append(f"SELECT {', '.join(columns)} FROM {view_name}")

        if not union_queries:
            logger.error(
                "No valid markets with data found",
                requested_markets=markets,
                available_markets=[m for m, d in self.MARKET_DIRS.items() if d.exists()],
            )
            return pl.DataFrame()

        import pandas as pd

        self.conn.register("selected_tickers", pd.DataFrame({"ticker": tickers}))
        sql = """
        WITH unioned AS (
            {union_all}
        )
        SELECT unioned.*
        FROM unioned
        JOIN selected_tickers USING (ticker)
        WHERE date >= $1
          AND date <= $2
        ORDER BY ticker, date
        """.format(union_all=" UNION ALL ".join(union_queries))

        logger.debug("Executing query", sql_preview=sql[:200] + "...")

        try:
            arrow_tbl = self.conn.execute(sql, [start_date, end_date]).fetch_arrow_table()
            return cast(pl.DataFrame, pl.from_arrow(arrow_tbl))
        except Exception as e:
            logger.error("Query failed", error=str(e))
            return pl.DataFrame()

    def _clean_data(
        self,
        data: pl.DataFrame,
        fill_method: str | None = "ffill",
    ) -> pl.DataFrame:
        if "date" in data.columns:
            data = data.with_columns(pl.col("date").cast(pl.Date))

        data = data.unique(subset=["ticker", "date"], keep="last")
        data = data.sort(["ticker", "date"])

        if fill_method:
            price_cols = [c for c in ["open", "high", "low", "close", "volume"] if c in data.columns]
            if price_cols:
                if fill_method == "ffill":
                    data = data.with_columns([pl.col(c).forward_fill().over("ticker") for c in price_cols])
                elif fill_method == "bfill":
                    data = data.with_columns([pl.col(c).backward_fill().over("ticker") for c in price_cols])

        price_cols = [c for c in ["open", "high", "low", "close"] if c in data.columns]
        if price_cols:
            data = data.filter(~pl.all_horizontal([pl.col(c).is_null() for c in price_cols]))

        return data

    @memory.cache  # type: ignore[untyped-decorator]
    def load_cached(
        self,
        tickers: tuple[str, ...],
        start_date: str,
        end_date: str,
        markets: tuple[str, ...],
        columns: tuple[str, ...],
    ) -> pl.DataFrame:
        return self.load(
            tickers=list(tickers),
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            markets=list(markets),
            columns=list(columns),
        )

    def get_available_tickers(
        self,
        market: str,
        as_of_date: date | None = None,
    ) -> list[str]:
        view_name = f"backtest_{market}"

        if as_of_date:
            sql = f"""
            SELECT DISTINCT ticker
            FROM {view_name}
            WHERE date = $1
            ORDER BY ticker
            """
            params = [as_of_date]
        else:
            sql = f"""
            SELECT DISTINCT ticker
            FROM {view_name}
            ORDER BY ticker
            """
            params = []

        try:
            rows = self.conn.execute(sql, params).fetchall()
            return [row[0] for row in rows]
        except Exception as e:
            logger.error("Failed to get tickers", market=market, error=str(e))
            return []

    def get_date_range(
        self,
        market: str,
        ticker: str | None = None,
    ) -> tuple[date | None, date | None]:
        view_name = f"backtest_{market}"

        if ticker:
            sql = f"""
            SELECT
                MIN(date) as min_date,
                MAX(date) as max_date
            FROM {view_name}
            WHERE ticker = $1
            """
            params = [ticker]
        else:
            sql = f"""
            SELECT
                MIN(date) as min_date,
                MAX(date) as max_date
            FROM {view_name}
            """
            params = []

        try:
            row = self.conn.execute(sql, params).fetchone()
            if row and row[0] is not None:
                return (
                    date.fromisoformat(str(row[0])),
                    date.fromisoformat(str(row[1])),
                )
        except Exception as e:
            logger.error("Failed to get date range", market=market, error=str(e))

        return (None, None)

    def clear_cache(self) -> None:
        memory.clear()
        logger.info("Cache cleared")

    def close(self) -> None:
        self.conn.close()
        logger.debug("DuckDB connection closed")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


__all__ = [
    "BacktestDataLoader",
    "CACHE_DIR",
]
