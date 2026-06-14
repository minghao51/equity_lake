"""StockTwits developer API fetcher for symbol-specific message streams."""

from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime
from typing import Any

import polars as pl
import requests
import structlog

from equity_lake.core.schemas import BRONZE_ARTICLE_COLUMNS
from equity_lake.sources.base import MarketDataFetcher, _empty_frame

logger = structlog.get_logger()

STOCKTWITS_API_URL = "https://api.stocktwits.com/api/2"


class StockTwitsFetcher(MarketDataFetcher):
    """Fetch symbol-specific message streams from StockTwits API.

    Uses the free developer API tier (200 req/hour, 40 messages per call).
    Messages already include bullish/bearish sentiment tags — this supplements
    the LLM extraction.
    """

    market = "stocktwits_messages"

    def __init__(
        self,
        tickers: list[str] | None = None,
        messages_per_symbol: int = 30,
        retry_attempts: int = 3,
        retry_delay: float = 2.0,
    ):
        super().__init__(retry_attempts, retry_delay)
        self.tickers = tickers or []
        self.messages_per_symbol = min(messages_per_symbol, 40)
        self.client_id = os.getenv("STOCKTWITS_CLIENT_ID")
        logger.info("Initialized StockTwitsFetcher", ticker_count=len(self.tickers))

    def fetch(self, trading_date: date) -> pl.DataFrame:
        if not self.tickers:
            logger.warning("No tickers configured for StockTwits")
            return _empty_frame()

        logger.info("Fetching StockTwits messages", ticker_count=len(self.tickers), trading_date=str(trading_date))

        all_messages: list[dict[str, Any]] = []
        for ticker in self.tickers:
            try:
                messages = self._fetch_symbol(ticker, trading_date)
                all_messages.extend(messages)
            except Exception as exc:
                logger.error("stocktwits_symbol_failed", ticker=ticker, error=str(exc))

        if not all_messages:
            logger.warning("No StockTwits messages fetched")
            return _empty_frame()

        df = pl.DataFrame(all_messages)

        for col in BRONZE_ARTICLE_COLUMNS:
            if col not in df.columns:
                df = df.with_columns(pl.lit(None).alias(col))

        df = df.select(BRONZE_ARTICLE_COLUMNS)
        logger.info("Fetched StockTwits messages", count=df.height)
        return df

    def _fetch_symbol(self, ticker: str, trading_date: date) -> list[dict[str, Any]]:
        def _fetch() -> list[dict[str, Any]]:
            url = f"{STOCKTWITS_API_URL}/streams/symbol/{ticker}.json"
            params: dict[str, Any] = {"limit": self.messages_per_symbol}
            if self.client_id:
                params["access_token"] = self.client_id

            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            data: dict[str, Any] = response.json()

            messages_data = data.get("messages", [])
            results: list[dict[str, Any]] = []

            for msg in messages_data:
                created_str = msg.get("created_at", "")
                published = _parse_timestamp(created_str)

                if published.date() < trading_date:
                    continue

                msg_id = str(msg.get("id", 0))
                article_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"stocktwits_{ticker}_{msg_id}"))

                body = msg.get("body", "").strip()
                sentiment_data = msg.get("entities", {}).get("sentiment", {})
                sentiment_label = sentiment_data.get("basic", "") if sentiment_data else None

                user = msg.get("user", {})
                metadata = {
                    "ticker": ticker,
                    "stocktwits_sentiment": sentiment_label,
                    "user_watchlist_count": user.get("watchlist_count", 0),
                    "user_followers": user.get("followers", 0),
                }

                results.append(
                    {
                        "article_id": article_id,
                        "source_type": "stocktwits",
                        "source_name": "stocktwits",
                        "source_url": f"https://stocktwits.com/message/{msg_id}",
                        "title": body[:200],
                        "body": body[:5000],
                        "author": user.get("username", ""),
                        "published_at": published,
                        "fetched_at": datetime.now(),
                        "source_metadata": json.dumps(metadata),
                        "date": published.date(),
                    }
                )

            logger.debug("stocktwits_symbol_parsed", ticker=ticker, messages=len(results))
            return results

        return self._retry_on_failure(_fetch)  # type: ignore[no-any-return]


def _parse_timestamp(ts: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(ts, fmt)
            return dt.replace(tzinfo=None) if dt.tzinfo else dt
        except (ValueError, TypeError):
            continue
    return datetime.now()


__all__ = ["StockTwitsFetcher"]
