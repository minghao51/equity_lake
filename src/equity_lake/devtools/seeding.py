"""Shared synthetic-seeding primitives: curated universes, business days, OHLCV.

Single polars home for the synthetic data generation that
``equity bootstrap sample`` (:mod:`equity_lake.cli.bootstrap`),
``equity demo seed`` (:mod:`equity_lake.devtools.seed_demo`) and the sandbox
test-data generator (:mod:`equity_lake.devtools.test_data`) each used to
implement separately (three OHLCV generators, three business-day helpers, three
curated ticker lists).

Callers keep their own :class:`OhlcvProfile` — the distribution parameters are
per-tool tuning, not shared policy — but the random walk, the Mon–Fri calendar,
and the curated universes live here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import polars as pl

# ---------------------------------------------------------------------------
# Curated universes
# ---------------------------------------------------------------------------

#: Small per-market subset used by ``equity bootstrap sample``.
SAMPLE_TICKERS: dict[str, list[str]] = {
    "us_equity": ["AAPL", "MSFT", "GOOGL", "NVDA", "JPM"],
    "cn_ashare": ["600519", "000001", "601318", "601398", "000858"],
    "hk_sg_equity": ["0700.HK", "9988.HK", "D05.SI", "0005.HK", "O39.SI"],
}

#: Built-in demo universe for ``equity demo seed`` — every symbol is defined &
#: active in ``config/tickers.yaml`` (markets.us), so the `demo` config group
#: resolves to these.
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

#: Larger per-market universes for the sandbox test-data generator.
TEST_DATA_TICKERS: dict[str, list[str]] = {
    "us_equity": [
        "AAPL",
        "MSFT",
        "GOOGL",
        "AMZN",
        "NVDA",
        "META",
        "TSLA",
        "BRK-B",
        "JPM",
        "V",
        "JNJ",
        "WMT",
        "MA",
        "PG",
        "UNH",
        "HD",
        "CVX",
        "MRK",
        "KO",
        "PEP",
        "COST",
        "CRM",
        "NFLX",
        "AMD",
        "TMO",
        "LIN",
        "ABT",
        "ORCL",
        "ADBE",
        "CMCSA",
        "WFC",
        "COP",
        "QCOM",
        "INTC",
        "DHR",
        "VZ",
        "IBM",
        "GE",
        "DIS",
        "BA",
        "NKE",
        "CAT",
        "XOM",
        "CSCO",
    ],
    "cn_ashare": [
        "600000",
        "600036",
        "601318",
        "601398",
        "601857",
        "601988",
        "601939",
        "601288",
        "601328",
        "601601",
        "601668",
        "601628",
        "601766",
        "601818",
        "601933",
        "601985",
        "601988",
        "602008",
        "000001",
        "000002",
        "000063",
        "000066",
        "000069",
        "000100",
        "000157",
        "000166",
        "000333",
        "000338",
        "000651",
        "000725",
        "000858",
        "000895",
        "002008",
        "002415",
        "002594",
        "002714",
    ],
    "hk_sg_equity": [
        "0700.HK",
        "9988.HK",
        "0941.HK",
        "1299.HK",
        "2318.HK",
        "0939.HK",
        "1398.HK",
        "0883.HK",
        "0857.HK",
        "1038.HK",
        "0027.HK",
        "0016.HK",
        "0005.HK",
        "0388.HK",
        "0011.HK",
        "D05.SI",
        "O39.SI",
        "U11.SI",
        "Z74.SI",
        "C6L.SI",
        "S68.SI",
        "V03.SI",
        "BS6.SI",
        "G13.SI",
        "S63.SI",
    ],
}


# ---------------------------------------------------------------------------
# Business-day calendar (weekday approximation — no exchange holidays)
# ---------------------------------------------------------------------------


def business_days(start: date, end: date) -> list[date]:
    """Mon–Fri dates from *start* to *end* inclusive."""
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def trailing_business_days(calendar_days: int, *, end: date | None = None) -> list[date]:
    """Mon–Fri dates over the *calendar_days* window ending *end* (default yesterday)."""
    end_date = end if end is not None else date.today() - timedelta(days=1)
    return business_days(end_date - timedelta(days=calendar_days), end_date)


# ---------------------------------------------------------------------------
# Synthetic OHLCV
# ---------------------------------------------------------------------------

#: Prices are floored here so a long negative walk can never produce <= 0.
PRICE_FLOOR = 0.01

_PRICE_COLUMNS = ("open", "high", "low", "close")


@dataclass(frozen=True)
class OhlcvProfile:
    """Distribution parameters for :func:`synthetic_ohlcv`.

    Attributes:
        price_range: Uniform range for each ticker's starting price.
        volume_range: Volume clip range; also the base-volume draw when
            ``volume_mu`` is ``None``.
        drift: Mean daily log return.
        volatility: Daily log-return standard deviation.
        range_scale: Std-dev of the (multiplicative) intraday high/low band.
        volume_sigma: Lognormal sigma for volumes.
        volume_mu: Fixed lognormal mu. ``None`` draws a base volume from
            ``volume_range`` and clips the series back into that range.
        gap_probability: Probability of an extra ``±gap_size`` return shock.
        gap_size: Magnitude of that shock.
        include_adj_close: Emit an ``adj_close`` column (equal to ``close``).
    """

    price_range: tuple[float, float] = (10.0, 500.0)
    volume_range: tuple[int, int] = (1_000_000, 50_000_000)
    drift: float = 0.0001
    volatility: float = 0.02
    range_scale: float = 0.01
    volume_sigma: float = 0.5
    volume_mu: float | None = None
    gap_probability: float = 0.0
    gap_size: float = 0.05
    include_adj_close: bool = True


DEFAULT_OHLCV_PROFILE = OhlcvProfile()


def ohlcv_schema(profile: OhlcvProfile = DEFAULT_OHLCV_PROFILE) -> dict[str, pl.DataType]:
    """Column schema produced by :func:`synthetic_ohlcv` for *profile*."""
    schema: dict[str, pl.DataType] = {
        "ticker": pl.Utf8(),
        "date": pl.Date(),
        **{column: pl.Float64() for column in _PRICE_COLUMNS},
        "volume": pl.Int64(),
    }
    if profile.include_adj_close:
        schema["adj_close"] = pl.Float64()
    return schema


def synthetic_ohlcv(
    tickers: Sequence[str],
    days: Sequence[date],
    *,
    profile: OhlcvProfile = DEFAULT_OHLCV_PROFILE,
    seed: int = 42,
    rng: np.random.Generator | None = None,
) -> pl.DataFrame:
    """Deterministic long-format OHLCV frame (geometric random walk per ticker).

    ``high``/``low`` bracket ``open``/``close`` by construction and every price
    is floored at :data:`PRICE_FLOOR`, so the result needs no post-hoc
    data-quality filtering.

    Args:
        tickers: Symbols to generate, in order.
        days: Trading dates for every ticker (see :func:`business_days`).
        profile: Distribution parameters.
        seed: Seed for the internally created RNG (ignored when *rng* is given).
        rng: Shared RNG. Pass one when generating several markets in sequence so
            each market draws from a distinct part of the stream.

    Returns:
        Frame with ``ticker, date, open, high, low, close, volume`` (plus
        ``adj_close`` when ``profile.include_adj_close``).
    """
    generator = rng if rng is not None else np.random.default_rng(seed)
    num_days = len(days)
    date_values = list(days)
    frames: list[pl.DataFrame] = []

    for ticker in tickers:
        start_price = float(generator.uniform(*profile.price_range))
        returns = generator.normal(profile.drift, profile.volatility, num_days)
        if profile.gap_probability > 0:
            gaps = generator.random(num_days) < profile.gap_probability
            returns[gaps] += generator.choice([-profile.gap_size, profile.gap_size], size=int(gaps.sum()))

        closes = np.maximum(start_price * np.exp(np.cumsum(returns)), PRICE_FLOOR)
        opens = np.roll(closes, 1)
        opens[0] = start_price
        span = np.abs(generator.normal(0.0, profile.range_scale, num_days))
        highs = np.maximum(opens, closes) * (1 + span)
        lows = np.maximum(np.minimum(opens, closes) * (1 - span), PRICE_FLOOR)

        if profile.volume_mu is not None:
            volumes = generator.lognormal(profile.volume_mu, profile.volume_sigma, num_days).astype(np.int64)
        else:
            base_volume = generator.uniform(*profile.volume_range)
            volumes = generator.lognormal(np.log(base_volume), profile.volume_sigma, num_days).astype(np.int64)
            volumes = np.clip(volumes, *profile.volume_range)

        columns: dict[str, object] = {
            "ticker": ticker,
            "date": date_values,
            "open": np.round(opens, 2),
            "high": np.round(highs, 2),
            "low": np.round(lows, 2),
            "close": np.round(closes, 2),
            "volume": volumes,
        }
        if profile.include_adj_close:
            columns["adj_close"] = np.round(closes, 2)
        frames.append(pl.DataFrame(columns, schema=ohlcv_schema(profile)))

    if not frames:
        return pl.DataFrame(schema=ohlcv_schema(profile))
    return pl.concat(frames)


__all__ = [
    "DEFAULT_OHLCV_PROFILE",
    "DEMO_UNIVERSE",
    "PRICE_FLOOR",
    "SAMPLE_TICKERS",
    "TEST_DATA_TICKERS",
    "OhlcvProfile",
    "business_days",
    "ohlcv_schema",
    "synthetic_ohlcv",
    "trailing_business_days",
]
