"""Finnhub analyst rating fetcher for US equities.

Fetches analyst recommendation trends and consensus price targets from
the Finnhub API. Data is already structured — no LLM processing needed.
Writes directly to the ``us_analyst_ratings`` Delta table.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from typing import Any

import httpx
import polars as pl
import structlog

from equity_lake.core.polars_utils import ensure_columns
from equity_lake.core.schemas import ANALYST_RATING_COLUMNS
from equity_lake.sources.base import MarketDataFetcher, TransientError, _empty_frame

logger = structlog.get_logger()

FINNHUB_BASE_URL = "https://finnhub.io/api/v1"


def _safe_error_type(exc: BaseException) -> str:
    """Return a bounded exception class name without serializing its message."""
    return type(exc).__name__[:80]


def _bounded_status_code(status_code: object) -> int | None:
    if isinstance(status_code, int) and not isinstance(status_code, bool) and 100 <= status_code <= 599:
        return status_code
    return None


def _safe_status_code(exc: BaseException) -> int | None:
    """Return only a valid HTTP status from an exception or its direct cause."""
    for candidate in (exc, exc.__cause__):
        if candidate is None:
            continue
        response = getattr(candidate, "response", None)
        status_code = getattr(candidate, "status_code", None)
        if status_code is None:
            status_code = getattr(response, "status_code", None)
        if (bounded_status_code := _bounded_status_code(status_code)) is not None:
            return bounded_status_code
    return None


class _AnalystRequestError(Exception):
    """Credential-safe request failure retaining only bounded diagnostic fields."""

    def __init__(self, endpoint: str, error_type: str, status_code: int | None):
        self.endpoint = endpoint[:40]
        self.error_type = error_type[:80]
        self.status_code = _bounded_status_code(status_code)
        super().__init__("Finnhub analyst ratings request failed")


class _TransientAnalystRequestError(_AnalystRequestError, TransientError):
    """Safe analyst request failure classified for the shared tenacity retry seam."""


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
            except _AnalystRequestError as exc:
                logger.error(
                    "analyst_rating_request_failed",
                    ticker=ticker,
                    endpoint=exc.endpoint,
                    error_type=exc.error_type,
                    status_code=exc.status_code,
                )
            except Exception as exc:
                logger.error(
                    "analyst_rating_failed",
                    ticker=ticker,
                    error_type=_safe_error_type(exc),
                    status_code=_safe_status_code(exc),
                )

        if not all_rows:
            logger.warning("No analyst ratings fetched")
            return _empty_frame()

        try:
            df = pl.DataFrame(all_rows)
            df = ensure_columns(df, ANALYST_RATING_COLUMNS)
        except Exception as exc:
            logger.error(
                "analyst_ratings_normalization_failed",
                error_type=_safe_error_type(exc),
                status_code=_safe_status_code(exc),
            )
            raise RuntimeError("Analyst ratings normalization failed") from None

        logger.info("Fetched analyst ratings", rows=df.height)
        return df

    def _fetch_ticker(self, ticker: str, trading_date: date) -> dict[str, Any] | None:
        def _fetch() -> dict[str, Any] | None:
            rec_data = self._get_recommendation(ticker)
            target_data = self._get_price_target(ticker)

            if not rec_data:
                return None

            latest = rec_data[0]
            if not isinstance(latest, dict):
                logger.warning(
                    "analyst_rating_invalid_recommendation_item",
                    ticker=ticker,
                    response_type=type(latest).__name__,
                )
                return None

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
                "fetched_at": datetime.now(UTC).replace(tzinfo=None),
            }

        return self._retry_on_failure(_fetch)  # type: ignore[no-any-return]

    def _get_json(self, ticker: str, endpoint: str) -> Any:
        url = f"{FINNHUB_BASE_URL}/stock/{endpoint}"
        params = {"symbol": ticker, "token": self.api_key}
        response: httpx.Response | None = None
        try:
            with httpx.Client(timeout=10) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                return response.json()
        except Exception as exc:
            status_code = _safe_status_code(exc)
            if status_code is None and response is not None:
                status_code = _bounded_status_code(response.status_code)
            retryable_status = status_code is not None and (status_code >= 500 or status_code in (408, 429))
            retryable_transport = isinstance(exc, (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError))
            error_type = _safe_error_type(exc)
            if retryable_status or retryable_transport:
                raise _TransientAnalystRequestError(endpoint, error_type, status_code) from None
            raise _AnalystRequestError(endpoint, error_type, status_code) from None

    def _get_recommendation(self, ticker: str) -> list[Any]:
        data = self._get_json(ticker, "recommendation")
        if not isinstance(data, list):
            logger.warning("analyst_rating_invalid_recommendation_response", ticker=ticker, response_type=type(data).__name__)
            return []
        return data

    def _get_price_target(self, ticker: str) -> dict[str, Any] | None:
        data = self._get_json(ticker, "price-target")
        if not isinstance(data, dict):
            logger.warning("analyst_rating_invalid_price_target_response", ticker=ticker, response_type=type(data).__name__)
            return None
        if not data:
            return None
        return data


__all__ = ["AnalystRatingFetcher"]
