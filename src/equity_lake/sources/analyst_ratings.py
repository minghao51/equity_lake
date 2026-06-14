"""Finnhub analyst rating fetcher for US equities.

Fetches analyst recommendation trends and consensus price targets from
the Finnhub API. Data is already structured — no LLM processing needed.
Writes directly to the ``us_analyst_ratings`` Delta table.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

import httpx
import polars as pl
import structlog

from equity_lake.core.schemas import ANALYST_RATING_COLUMNS
from equity_lake.sources.base import MarketDataFetcher, _empty_frame

logger = structlog.get_logger()

FINNHUB_BASE_URL = "https://finnhub.io/api/v1"


class AnalystRatingFetcher(MarketDataFetcher):
    """Fetch analyst recommendation trends and price targets from Finnhub.

    Uses two Finnhub endpoints per ticker:
        - ``/stock/recommendation`` — buy/hold/sell distribution
        - ``/stock/price-target`` — consensus price target

    Returns a structured DataFrame with ``ANALYST_RATING_COLUMNS``.
    """

    market = "us_analyst_ratings"

    def __init__(
        self,
        api_key: str | None = None,
        tickers: list[str] | None = None,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
    ):
        super().__init__(retry_attempts, retry_delay)
        self.api_key = api_key or os.getenv("FINNHUB_API_KEY")
        if not self.api_key:
            raise ValueError("FINNHUB_API_KEY not set")
        self.tickers = tickers or []
        logger.info("Initialized AnalystRatingFetcher", ticker_count=len(self.tickers))

    def fetch(self, trading_date: date) -> pl.DataFrame:
        if not self.tickers:
            logger.warning("No tickers configured for analyst ratings")
            return _empty_frame()

        logger.info("Fetching analyst ratings", ticker_count=len(self.tickers), trading_date=str(trading_date))

        all_rows: list[dict[str, Any]] = []
        for ticker in self.tickers:
            try:
                row = self._fetch_ticker(ticker, trading_date)
                if row:
                    all_rows.append(row)
            except Exception as exc:
                logger.error("analyst_rating_failed", ticker=ticker, error=str(exc))

        if not all_rows:
            logger.warning("No analyst ratings fetched")
            return _empty_frame()

        df = pl.DataFrame(all_rows)
        for col in ANALYST_RATING_COLUMNS:
            if col not in df.columns:
                df = df.with_columns(pl.lit(None).alias(col))

        df = df.select(ANALYST_RATING_COLUMNS)
        logger.info("Fetched analyst ratings", rows=df.height)
        return df

    def _fetch_ticker(self, ticker: str, trading_date: date) -> dict[str, Any] | None:
        def _fetch() -> dict[str, Any] | None:
            rec_data = self._get_recommendation(ticker)
            target_data = self._get_price_target(ticker)

            if not rec_data:
                return None

            latest = rec_data[0]
            strong_buy = int(latest.get("strongBuy", 0))
            buy = int(latest.get("buy", 0))
            hold = int(latest.get("hold", 0))
            sell = int(latest.get("sell", 0))
            strong_sell = int(latest.get("strongSell", 0))

            total = strong_buy + buy + hold + sell + strong_sell
            if total == 0:
                consensus_score = 0.0
                consensus_label = "hold"
            else:
                consensus_score = (strong_buy * 2 + buy * 1 + hold * 0 + sell * -1 + strong_sell * -2) / total
                counts = {
                    "strong_buy": strong_buy,
                    "buy": buy,
                    "hold": hold,
                    "sell": sell,
                    "strong_sell": strong_sell,
                }
                consensus_label = max(counts, key=lambda k: counts[k])

            period = latest.get("period", str(trading_date))

            price_target = target_data or {}
            return {
                "ticker": ticker,
                "date": trading_date,
                "period": period,
                "strong_buy": strong_buy,
                "buy": buy,
                "hold": hold,
                "sell": sell,
                "strong_sell": strong_sell,
                "consensus_score": round(consensus_score, 4),
                "consensus_label": consensus_label,
                "price_target_mean": price_target.get("targetMean"),
                "price_target_median": price_target.get("targetMedian"),
                "price_target_high": price_target.get("targetHigh"),
                "price_target_low": price_target.get("targetLow"),
                "price_target_count": price_target.get("numberOfAnalysts", 0),
                "fetched_at": datetime.now(),
            }

        return self._retry_on_failure(_fetch)  # type: ignore[no-any-return]

    def _get_recommendation(self, ticker: str) -> list[dict[str, Any]]:
        url = f"{FINNHUB_BASE_URL}/stock/recommendation"
        params = {"symbol": ticker, "token": self.api_key}
        with httpx.Client(timeout=10) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data: Any = resp.json()
        if not isinstance(data, list):
            return []
        return data

    def _get_price_target(self, ticker: str) -> dict[str, Any] | None:
        url = f"{FINNHUB_BASE_URL}/stock/price-target"
        params = {"symbol": ticker, "token": self.api_key}
        with httpx.Client(timeout=10) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data: Any = resp.json()
        if not isinstance(data, dict) or not data:
            return None
        return data


__all__ = ["AnalystRatingFetcher"]
