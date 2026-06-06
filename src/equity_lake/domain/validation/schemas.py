"""Pandera schema definitions for data quality validation."""

from __future__ import annotations

import pandas as pd
import pandera as pa
from pandera.typing import Series


class PriceDataSchema(pa.DataFrameModel):
    """Schema for OHLCV price data.

    Validates required columns, positive prices, price consistency,
    and no duplicate ticker+date combinations.
    """

    ticker: Series[str] = pa.Field(description="Ticker symbol")
    date: Series[pd.Timestamp] = pa.Field(description="Trading date")
    open: Series[float] = pa.Field(gt=0, description="Opening price")
    high: Series[float] = pa.Field(gt=0, description="High price")
    low: Series[float] = pa.Field(gt=0, description="Low price")
    close: Series[float] = pa.Field(gt=0, description="Closing price")
    volume: Series[float] = pa.Field(ge=0, description="Volume")
    adj_close: Series[float] | None = pa.Field(gt=0, default=None, description="Adjusted close")

    @pa.dataframe_check
    @classmethod
    def price_consistency(cls, df: pd.DataFrame) -> Series[bool]:
        """High >= Low, High >= Open/Close, Low <= Open/Close."""
        return (  # type: ignore[no-any-return]
            (df["high"] >= df["low"])
            & (df["high"] >= df["open"])
            & (df["high"] >= df["close"])
            & (df["low"] <= df["open"])
            & (df["low"] <= df["close"])
        )

    @pa.dataframe_check
    @classmethod
    def no_duplicates(cls, df: pd.DataFrame) -> Series[bool]:
        """No duplicate ticker+date combinations."""
        return ~df.duplicated(subset=["ticker", "date"])  # type: ignore[no-any-return]

    class Config:
        coerce = True
        strict = False


class MacroDataSchema(pa.DataFrameModel):
    """Schema for macro economic indicator data."""

    date: Series[pd.Timestamp] = pa.Field(description="Observation date")
    indicator: Series[str] = pa.Field(description="Indicator name")
    value: Series[float] = pa.Field(description="Indicator value")
    source: Series[str] = pa.Field(description="Data source")
    updated_at: Series[str] | None = pa.Field(default=None, description="Last updated timestamp")

    class Config:
        coerce = True
        strict = False


class NewsDataSchema(pa.DataFrameModel):
    """Schema for news article data with sentiment."""

    ticker: Series[str] = pa.Field(description="Stock symbol")
    date: Series[pd.Timestamp] = pa.Field(description="Published date")
    datetime: Series[pd.Timestamp] = pa.Field(description="Exact publication timestamp")
    source: Series[str] = pa.Field(description="News source")
    headline: Series[str] = pa.Field(description="Article title")
    summary: Series[str] | None = pa.Field(default=None, description="Article summary")
    url: Series[str] = pa.Field(description="Article URL")
    category: Series[str] | None = pa.Field(default=None, description="News category")
    sentiment_score: Series[float] = pa.Field(ge=-1, le=1, description="VADER score")
    sentiment_label: Series[str] = pa.Field(description="Sentiment label")
    relevance_score: Series[float] | None = pa.Field(ge=0, le=1, default=None, description="API relevance score")

    @pa.dataframe_check
    @classmethod
    def no_duplicate_urls(cls, df: pd.DataFrame) -> Series[bool]:
        """No duplicate article URLs."""
        return ~df.duplicated(subset=["url"])  # type: ignore[no-any-return]

    class Config:
        coerce = True
        strict = False


SCHEMA_REGISTRY: dict[str, type[pa.DataFrameModel]] = {
    "price": PriceDataSchema,
    "macro": MacroDataSchema,
    "news": NewsDataSchema,
}
