from __future__ import annotations

from datetime import date
from typing import Any, Literal, Self, cast

import duckdb
import polars as pl
import structlog

from equity_lake.core.paths import PRICE_MARKETS, market_dir
from equity_lake.storage.lake_reader import create_market_views, with_price_adjustment

logger = structlog.get_logger(__name__)

AdjustmentMode = Literal["none", "splits", "total_return"]
_ADJUST_METHODS: dict[str, Literal["split_only", "total_return"]] = {
    "splits": "split_only",
    "total_return": "total_return",
}


class BacktestDataLoader:
    """Loads OHLCV data from the price-market registry directories (ADR-0010).

    View and market keys are the canonical long forms (``backtest_us_equity``).
    """

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
        )

    def _setup_views(self) -> None:
        logger.debug("Setting up market views...")
        create_market_views(
            self.conn,
            view_prefix="backtest_",
            columns=["ticker", "date", "open", "high", "low", "close", "volume"],
        )

    def load(
        self,
        tickers: list[str],
        start_date: date,
        end_date: date,
        markets: list[str] | None = None,
        columns: list[str] | None = None,
        adjust: AdjustmentMode = "none",
    ) -> pl.DataFrame:
        """Load long-format OHLCV data, forward-filling per-ticker gaps.

        Only forward fill is supported: back fill would leak future prices into
        past rows (lookahead bias) and is intentionally not offered.

        ``adjust`` (ADR-0011) back-adjusts OHLC for corporate actions at read
        time: ``"splits"`` fixes split discontinuities; ``"total_return"``
        additionally reinvests dividends. ``"none"`` (default) returns the
        stored raw prices unchanged. Adjustment is a no-op (with a warning)
        when the market has no corporate-actions table yet.
        """
        if markets is None:
            markets = list(PRICE_MARKETS)

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

        data = self._clean_data(data)

        if adjust != "none":
            data = self._apply_adjustment(data, markets, adjust)

        logger.debug(
            "Returned long format",
            shape=data.shape,
            tickers=data["ticker"].n_unique(),
        )

        return data

    def _apply_adjustment(self, data: pl.DataFrame, markets: list[str], adjust: AdjustmentMode) -> pl.DataFrame:
        """Back-adjust loaded OHLC for corporate actions (ADR-0011, opt-in)."""
        if not {"ticker", "date", "close"}.issubset(data.columns):
            logger.warning("adjustment_skipped_missing_columns", columns=data.columns)
            return data
        actions = self._load_corporate_actions(markets)
        if actions is None or actions.is_empty():
            logger.warning("corporate_actions_table_missing_adjustment_noop", adjust=adjust, markets=markets)
            return data
        method: Literal["split_only", "total_return"] = _ADJUST_METHODS[adjust]
        return with_price_adjustment(data, actions, method=method)

    @staticmethod
    def _load_corporate_actions(markets: list[str]) -> pl.DataFrame | None:
        """Union the per-market silver corporate-actions tables (missing → None)."""
        from equity_lake.ingestion.corporate_actions import corporate_actions_table
        from equity_lake.storage.delta import DeltaReadError, read_delta

        frames: list[pl.DataFrame] = []
        for market in markets:
            try:
                frames.append(read_delta(corporate_actions_table(market, "silver")))
            except DeltaReadError:
                logger.debug("corporate_actions_table_absent", market=market)
        if not frames:
            return None
        return pl.concat(frames, how="vertical")

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
            data_dir = market_dir(market) if market in PRICE_MARKETS else None

            if market in ("jpx_equity", "krx_equity") and (not data_dir or not data_dir.exists()):
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
                available_markets=[m for m in PRICE_MARKETS if market_dir(m).exists()],
            )
            return pl.DataFrame()

        self.conn.register("selected_tickers", pl.DataFrame({"ticker": tickers}))
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
    ) -> pl.DataFrame:
        """Dedupe, sort, forward-fill per-ticker gaps, and drop all-null price rows."""
        if "date" in data.columns:
            data = data.with_columns(pl.col("date").cast(pl.Date))

        data = data.unique(subset=["ticker", "date"], keep="last")
        data = data.sort(["ticker", "date"])

        price_cols = [c for c in ["open", "high", "low", "close", "volume"] if c in data.columns]
        if price_cols:
            data = data.with_columns([pl.col(c).forward_fill().over("ticker") for c in price_cols])

        price_cols = [c for c in ["open", "high", "low", "close"] if c in data.columns]
        if price_cols:
            data = data.filter(~pl.all_horizontal([pl.col(c).is_null() for c in price_cols]))

        return data

    def close(self) -> None:
        self.conn.close()
        logger.debug("DuckDB connection closed")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


__all__ = [
    "BacktestDataLoader",
]
