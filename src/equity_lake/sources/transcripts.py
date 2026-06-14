"""Finnhub earnings call transcript fetcher for US equities.

Fetches quarterly earnings call transcripts from the Finnhub API.
Transcripts go through the bronze→silver LLM pipeline for sentiment
and management tone extraction.

The Finnhub transcript endpoint may require a premium API key on some
tiers. The fetcher degrades gracefully — returns an empty frame when
the endpoint is unavailable.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime
from typing import Any

import httpx
import polars as pl
import structlog

from equity_lake.core.schemas import BRONZE_ARTICLE_COLUMNS
from equity_lake.sources.base import MarketDataFetcher, _empty_frame

logger = structlog.get_logger()

FINNHUB_BASE_URL = "https://finnhub.io/api/v1"


class EarningsTranscriptFetcher(MarketDataFetcher):
    """Fetch earnings call transcripts from Finnhub.

    Uses the ``/stock/earnings-call-transcripts`` endpoint to retrieve
    full transcript text for each ticker. Transcripts are stored in
    bronze article schema (``source_type="transcript"``) for downstream
    LLM processing via the existing bronze→silver pipeline.

    The endpoint is checked for the current year only. Most tickers will
    have transcripts only during earnings seasons (4× per year), so
    daily runs will frequently return empty results.
    """

    market = "us_earnings_transcripts"

    def __init__(
        self,
        api_key: str | None = None,
        tickers: list[str] | None = None,
        year: int | None = None,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
    ):
        super().__init__(retry_attempts, retry_delay)
        self.api_key = api_key or os.getenv("FINNHUB_API_KEY")
        if not self.api_key:
            raise ValueError("FINNHUB_API_KEY not set")
        self.tickers = tickers or []
        self.year = year or datetime.now().year
        logger.info("Initialized EarningsTranscriptFetcher", ticker_count=len(self.tickers), year=self.year)

    def fetch(self, trading_date: date) -> pl.DataFrame:
        if not self.tickers:
            logger.warning("No tickers configured for earnings transcripts")
            return _empty_frame()

        logger.info("Fetching earnings transcripts", ticker_count=len(self.tickers), year=self.year)

        all_articles: list[dict[str, Any]] = []
        for ticker in self.tickers:
            try:
                articles = self._fetch_ticker(ticker, trading_date)
                all_articles.extend(articles)
            except Exception as exc:
                logger.error("transcript_fetch_failed", ticker=ticker, error=str(exc))

        if not all_articles:
            logger.info("No earnings transcripts found (typical outside earnings season)")
            return _empty_frame()

        df = pl.DataFrame(all_articles)
        for col in BRONZE_ARTICLE_COLUMNS:
            if col not in df.columns:
                df = df.with_columns(pl.lit(None).alias(col))

        df = df.select(BRONZE_ARTICLE_COLUMNS)
        logger.info("Fetched earnings transcripts", count=df.height)
        return df

    def _fetch_ticker(self, ticker: str, trading_date: date) -> list[dict[str, Any]]:
        def _fetch() -> list[dict[str, Any]]:
            url = f"{FINNHUB_BASE_URL}/stock/earnings-call-transcripts"
            params = {"symbol": ticker, "year": str(self.year), "token": self.api_key}

            with httpx.Client(timeout=15) as client:
                resp = client.get(url, params=params)
                if resp.status_code == 403:
                    logger.info("transcript_endpoint_forbidden", ticker=ticker, hint="may require premium Finnhub tier")
                    return []
                resp.raise_for_status()
                data: Any = resp.json()

            if not isinstance(data, list):
                return []

            results: list[dict[str, Any]] = []
            now = datetime.now()

            for transcript in data:
                transcript_id = str(transcript.get("id", ""))
                if not transcript_id:
                    continue

                title = transcript.get("title", f"{ticker} Earnings Call")
                time_str = transcript.get("time", "")
                published = _parse_time(time_str, fallback=trading_date)

                sections = transcript.get("transcript", [])
                if isinstance(sections, list):
                    body_parts = [s.get("text", "") for s in sections if isinstance(s, dict)]
                    body = "\n\n".join(body_parts)
                elif isinstance(sections, str):
                    body = sections
                else:
                    body = ""

                if not body.strip():
                    continue

                participants = (
                    [s.get("speaker", s.get("name", "")) for s in sections if isinstance(s, dict) and (s.get("speaker") or s.get("name"))]
                    if isinstance(sections, list)
                    else []
                )

                metadata = {
                    "quarter": transcript.get("quarter"),
                    "fiscal_year": transcript.get("year"),
                    "participants": list(dict.fromkeys(participants)),
                    "ticker": ticker,
                }

                article_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"finnhub_transcript_{transcript_id}"))

                results.append(
                    {
                        "article_id": article_id,
                        "source_type": "transcript",
                        "source_name": "finnhub",
                        "source_url": f"https://finnhub.io/transcript/{transcript_id}",
                        "title": title.strip(),
                        "body": body[:5000],
                        "author": "",
                        "published_at": published,
                        "fetched_at": now,
                        "source_metadata": json.dumps(metadata),
                        "date": published.date(),
                    }
                )

            logger.debug("transcript_ticker_parsed", ticker=ticker, count=len(results))
            return results

        return self._retry_on_failure(_fetch)  # type: ignore[no-any-return]


def _parse_time(ts: Any, fallback: date) -> datetime:
    if isinstance(ts, int):
        try:
            return datetime.fromtimestamp(ts)
        except (ValueError, OSError):
            pass
    if isinstance(ts, str):
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
            try:
                return datetime.strptime(ts, fmt)
            except (ValueError, TypeError):
                continue
    return datetime.combine(fallback, datetime.min.time())


__all__ = ["EarningsTranscriptFetcher"]
